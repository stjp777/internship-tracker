"""Render the shared postings list as a self-contained static HTML page.

Used by the GitHub Actions job to publish a read-only feed to GitHub Pages
after each poll. All filtering happens client-side, so the page needs no
server. Statuses are personal and therefore absent here — this page is a
feed, not a tracker. The page itself lives in feed.html.
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .locations import split_states

TEMPLATE_PATH = Path(__file__).with_name("feed.html")
_DOMAIN_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")


def html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def company_sites(cfg):
    """Company name -> logo domain, from each company's `website:` in
    config.yaml. Anything that isn't a plain domain is dropped, so only a
    trusted, well-formed hostname ever reaches the logo url."""
    sites = {}
    for c in (cfg or {}).get("companies", []):
        site = str(c.get("website", "")).strip().lower()
        if _DOMAIN_RE.match(site):
            sites[c["name"].lower()] = site
    return sites


def _health_html(conn):
    """Footer naming any source that is currently failing, so a broken
    adapter (e.g. Meta rotating its GraphQL doc_id) shows up on the page
    instead of only in the Actions log."""
    try:
        rows = conn.execute("SELECT * FROM source_health ORDER BY source").fetchall()
    except Exception:
        return ""
    names = lambda rs: ", ".join(sorted(r["source"].replace("career:", "") for r in rs))
    broken = [r for r in rows if (r["error_msg"] or "").strip()]
    ok = [r for r in rows if not (r["error_msg"] or "").strip()]
    parts = []
    if broken:
        parts.append('<p class="warn">Temporarily failing, so these may be missing postings: '
                     f"{html_escape(names(broken))}.</p>")
    if ok:
        parts.append(f"<p>Tracking {len(ok)} sources: {html_escape(names(ok))}.</p>")
    return "\n      ".join(parts)


def render(conn, out_path, cfg=None):
    rows = conn.execute(
        "SELECT * FROM postings"
        " WHERE status != 'Dismissed' AND COALESCE(removed_at, '') = ''"
        " ORDER BY first_seen DESC, id DESC LIMIT 1000").fetchall()
    sites = company_sites(cfg)
    data = [{
        "seen": r["first_seen"] or "",
        "company": r["company"],
        "title": r["title"],
        "url": r["url"],
        "location": (r["location"] or "")[:80],
        "states": split_states(r["state"]),
        "cats": json.loads(r["categories"] or "[]") or ["general"],
        "site": sites.get((r["company"] or "").lower(), ""),
    } for r in rows]

    now = datetime.now(timezone.utc)
    values = {
        "UPDATED": now.strftime("%b %d, %H:%M UTC"),
        "UPDATED_ISO": now.isoformat(timespec="seconds"),
        "HEALTH": _health_html(conn),
        # Escaping every "<" keeps a posting containing "</script>" or "<!--"
        # from ending the data block early; JSON.parse reads it back unchanged.
        "DATA": json.dumps(data).replace("<", "\\u003c"),
    }
    # One pass, so a substituted value is never scanned for another placeholder.
    html = re.sub(r"__(UPDATED_ISO|UPDATED|HEALTH|DATA)__",
                  lambda m: values[m.group(1)],
                  TEMPLATE_PATH.read_text(encoding="utf-8"))
    Path(out_path).write_text(html, encoding="utf-8")
    return len(data)
