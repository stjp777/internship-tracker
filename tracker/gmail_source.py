"""Pull LinkedIn / Indeed job-alert emails from Gmail and extract postings.

Requires a Google Cloud OAuth client file (credentials.json). Until that
file exists this module is silently skipped. Scope is read-only.
"""
import base64
import html as htmllib
import re
from pathlib import Path
from urllib.parse import urlsplit

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def gmail_available(cfg):
    g = cfg.get("gmail", {})
    return g.get("enabled", False) and Path(g.get("credentials", "credentials.json")).exists()


def _service(cfg):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    g = cfg["gmail"]
    token_path = Path(g.get("token", "token.json"))
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(g["credentials"], SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _walk_parts(payload):
    if payload.get("body", {}).get("data"):
        yield payload
    for p in payload.get("parts", []) or []:
        yield from _walk_parts(p)


def _decode_body(msg):
    """Longest text/html part, falling back to the longest part of any type.

    Job links live in the HTML alternative; the plain-text one usually
    strips them, so an HTML part always beats a longer plain-text part.
    """
    best_html, best_any = "", ""
    for part in _walk_parts(msg.get("payload", {})):
        data = part.get("body", {}).get("data", "")
        try:
            text = base64.urlsafe_b64decode(data + "===").decode("utf-8", "replace")
        except Exception:
            continue
        if part.get("mimeType") == "text/html" and len(text) > len(best_html):
            best_html = text
        if len(text) > len(best_any):
            best_any = text
    return best_html or best_any


def _strip_tags(fragment):
    # Strip again after unescaping: "&lt;img onerror=...&gt;" survives the
    # first pass untouched and would otherwise come back out as live markup.
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


# Path patterns that identify a job link per provider, matched against the
# url's path only — see _is_job_link, which checks scheme and host separately.
# Matching these anywhere in the raw href would accept a phishing link like
# "https://evil.example/login?next=linkedin.com/jobs/view/1", which then goes
# out to everyone's Discord and onto the public feed.
JOB_LINK_PATTERNS = {
    "linkedin": re.compile(r"^/(?:comm/)?jobs/view/\d+", re.I),
    "indeed": re.compile(r"^/(?:rc/clk|pagead/clk|viewjob|m/rc)", re.I),
}
JOB_LINK_HOSTS = {"linkedin": "linkedin.com", "indeed": "indeed.com"}


def _is_job_link(href, provider):
    parts = urlsplit(href)
    if parts.scheme.lower() not in ("http", "https"):
        return False
    host = (parts.hostname or "").lower()
    domain = JOB_LINK_HOSTS[provider]
    if host != domain and not host.endswith("." + domain):
        return False
    return bool(JOB_LINK_PATTERNS[provider].search(parts.path))


def _provider_for(sender):
    s = (sender or "").lower()
    if "linkedin" in s:
        return "linkedin"
    if "indeed" in s:
        return "indeed"
    return None


def extract_jobs_from_html(body_html, provider):
    """Best-effort extraction of (title, company, url) from an alert email."""
    jobs, seen_urls = [], set()
    for m in re.finditer(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', body_html, re.S | re.I):
        href, inner = m.group(1), m.group(2)
        # unescape first: the check must see the same url the browser would
        url = htmllib.unescape(href)
        if not _is_job_link(url, provider):
            continue
        # canonical key: LinkedIn job id, or indeed jk= param, else the URL
        key = url
        lm = re.search(r"jobs/view/(\d+)", url)
        im = re.search(r"[?&]jk=([0-9a-f]+)", url)
        if lm:
            key = f"li-{lm.group(1)}"
        elif im:
            key = f"in-{im.group(1)}"
        if key in seen_urls:
            continue
        title = _strip_tags(inner)
        if not title or len(title) < 4 or re.match(r"(?i)(view|see|apply|more jobs|unsubscribe)", title):
            continue
        seen_urls.add(key)
        # company/location usually follow the title link: "Company · Location"
        tail = _strip_tags(body_html[m.end():m.end() + 500])
        company = ""
        cm = re.match(r"([^·|•\-–]{2,60})[·|•]", tail)
        if cm:
            company = cm.group(1).strip()
        jobs.append({"title": title[:200], "company": company[:100], "url": url})
    return jobs


def fetch_alert_jobs(cfg, conn):
    """Returns list of {title, company, url, provider, gmail_id}. Marks
    emails as seen in the db so each is parsed only once."""
    from . import db

    g = cfg["gmail"]
    service = _service(cfg)
    senders = g.get("senders", [])
    lookback = int(g.get("initial_lookback_days", 3))
    query = "(" + " OR ".join(f"from:{s}" for s in senders) + f") newer_than:{lookback}d"

    results, page_token = [], None
    while True:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=50, pageToken=page_token).execute()
        for meta in resp.get("messages", []):
            if db.email_seen(conn, meta["id"]):
                continue
            msg = service.users().messages().get(
                userId="me", id=meta["id"], format="full").execute()
            headers = {h["name"].lower(): h["value"]
                       for h in msg.get("payload", {}).get("headers", [])}
            provider = _provider_for(headers.get("from", ""))
            if provider:
                body = _decode_body(msg)
                for job in extract_jobs_from_html(body, provider):
                    job["provider"] = provider
                    job["gmail_id"] = meta["id"]
                    results.append(job)
            db.mark_email_seen(conn, meta["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results
