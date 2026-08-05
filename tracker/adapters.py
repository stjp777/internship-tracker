"""Career-page adapters. Each returns a list of dicts:
{title, url, location, posted_at, description}

Adapter types: greenhouse, lever, ashby, workday, eightfold, amazon,
apple, google, meta. Add new companies in config.yaml; only add code here
for a genuinely new ATS.
"""
import html as htmllib
import json
import re

from .http_util import BROWSER_HEADERS, make_session, robots_allows

TIMEOUT = 30


def fetch_greenhouse(company, session):
    board = company["board"]
    r = session.get(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
        params={"content": "true"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append({
            "title": j.get("title", ""),
            "url": j.get("absolute_url", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "posted_at": (j.get("updated_at") or "")[:10],
            "description": htmllib.unescape(re.sub(r"<[^>]+>", " ", j.get("content", "")))[:4000],
        })
    return out


def fetch_lever(company, session):
    org = company["board"]
    r = session.get(f"https://api.lever.co/v0/postings/{org}",
                    params={"mode": "json"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        out.append({
            "title": j.get("text", ""),
            "url": j.get("hostedUrl", ""),
            "location": (j.get("categories") or {}).get("location", ""),
            "posted_at": "",
            "description": re.sub(r"<[^>]+>", " ", j.get("descriptionPlain") or j.get("description", ""))[:4000],
        })
    return out


def fetch_ashby(company, session):
    org = company["board"]
    r = session.get(
        f"https://api.ashbyhq.com/posting-api/job-board/{org}", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append({
            "title": j.get("title", ""),
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "location": j.get("location", ""),
            "posted_at": (j.get("publishedAt") or "")[:10],
            "description": re.sub(r"<[^>]+>", " ", j.get("descriptionHtml", ""))[:4000],
        })
    return out


def fetch_workday(company, session):
    host, tenant, site = company["host"], company["tenant"], company["site"]
    url = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    out, offset, seen = [], 0, set()
    while offset < 400:  # relevance-sorted; intern roles surface early
        r = session.post(url, json={
            "appliedFacets": {}, "limit": 20, "offset": offset,
            "searchText": company.get("search", "intern"),
        }, timeout=TIMEOUT)
        r.raise_for_status()
        posts = r.json().get("jobPostings", [])
        if not posts:
            break
        added = 0
        for j in posts:
            path = j.get("externalPath", "")
            if path in seen:
                continue  # some tenants ignore `offset` and loop the same page
            seen.add(path)
            added += 1
            out.append({
                "title": j.get("title", ""),
                "url": f"https://{host}/en-US/{site}{path}",
                "location": j.get("locationsText", ""),
                "posted_at": j.get("postedOn", ""),
                "description": "",
            })
        if added == 0:
            break
        offset += 20
    return out


def fetch_eightfold(company, session):
    host = company["host"]
    out, start = [], 0
    while start < 100:  # API returns 10 per page
        params = {"domain": company["domain"], "query": company.get("search", "intern"),
                  "start": start, "num": 10, "sort_by": "timestamp"}
        if company.get("location"):
            params["location"] = company["location"]
        r = session.get(
            f"https://{host}/api/pcsx/search", params=params,
            timeout=TIMEOUT)
        r.raise_for_status()
        positions = r.json().get("data", {}).get("positions", [])
        if not positions:
            break
        for p in positions:
            out.append({
                "title": p.get("name", ""),
                "url": f"https://{host}/careers/job/{p.get('id')}",
                "location": "; ".join(p.get("locations", [])[:3]),
                "posted_at": "",
                "description": "",
            })
        start += 10
    return out


def fetch_amazon(company, session):
    params = {"base_query": company.get("search", "intern"), "sort": "recent",
              "result_limit": 100, "offset": 0}
    if company.get("category"):
        params["category[]"] = company["category"]
    r = session.get(
        "https://www.amazon.jobs/en/search.json", params=params,
        timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json().get("jobs", []):
        out.append({
            "title": j.get("title", ""),
            "url": "https://www.amazon.jobs" + (j.get("job_path") or ""),
            "location": j.get("normalized_location") or j.get("location", ""),
            "posted_at": j.get("posted_date", ""),
            "description": (j.get("description") or "")[:2000]
                           + " " + (j.get("basic_qualifications") or "")[:2000],
        })
    return out


def fetch_apple(company, session):
    query = company.get("search", "internship")
    page_url = f"https://jobs.apple.com/en-us/search?search={query}&sort=newest"
    if company.get("location"):
        page_url += f"&location={company['location']}"
    if not robots_allows(page_url):
        raise RuntimeError("robots.txt disallows fetching Apple search page")
    out = []
    for page in (1, 2, 3):
        r = session.get(f"{page_url}&page={page}", timeout=TIMEOUT)
        r.raise_for_status()
        m = re.search(r'__staticRouterHydrationData\s*=\s*JSON\.parse\((".*?")\);', r.text, re.S)
        if not m:
            break
        data = json.loads(json.loads(m.group(1)))
        results = (data.get("loaderData", {}).get("search", {}) or {}).get("searchResults", [])
        if not results:
            break
        for j in results:
            out.append({
                "title": j.get("postingTitle", ""),
                "url": f"https://jobs.apple.com/en-us/details/{j.get('positionId')}/"
                       f"{j.get('transformedPostingTitle', '')}",
                "location": (j.get("locations") or [{}])[0].get("name", ""),
                "posted_at": j.get("postingDate", ""),
                "description": (j.get("jobSummary") or "")[:4000],
            })
        if len(results) < 20:
            break
    return out


def fetch_google(company, session):
    base = "https://www.google.com/about/careers/applications/jobs/results/"
    if not robots_allows(base + "?q=intern"):
        raise RuntimeError("robots.txt disallows fetching Google careers page")
    out, seen = [], set()
    for page in (1, 2, 3, 4, 5):
        params = {"q": company.get("search", "intern"),
                  "target_level": "INTERN_AND_APPRENTICE", "page": page}
        if company.get("location"):
            params["location"] = company["location"]
        r = session.get(base, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        t = r.text
        # Pair each job link with the title from the SAME anchor rather than
        # zipping two independent lists — Google interleaves other
        # aria-labelled links ("remote eligibility", share buttons) between
        # cards, and positional matching would mislabel every later job.
        pairs = []
        for m in re.finditer(
                r'href="(?:\./)?jobs/results/(\d+)-([a-z0-9-]+)[^"]*"[^>]*'
                r'aria-label="Learn more about ([^"]+)"', t):
            pairs.append((m.group(1), m.group(2), htmllib.unescape(m.group(3))))
        if not pairs:
            # Fall back to link-only extraction with slug-derived titles.
            for jid, slug in dict.fromkeys(
                    re.findall(r'href="(?:\./)?jobs/results/(\d+)-([a-z0-9-]+)', t)):
                pairs.append((jid, slug, slug.replace("-", " ").title()))
        if not pairs:
            break
        new_on_page = 0
        for jid, slug, title in pairs:
            if jid in seen:
                continue
            seen.add(jid)
            new_on_page += 1
            out.append({
                "title": title,
                "url": f"https://www.google.com/about/careers/applications/jobs/results/{jid}-{slug}",
                "location": "",
                "posted_at": "",
                "description": "",
            })
        if new_on_page == 0:
            break
    return out


def fetch_meta(company, session):
    # Meta requires realistic browser headers and a per-session `lsd` token.
    lsd = None
    for _ in range(3):
        r = session.get("https://www.metacareers.com/jobs", timeout=TIMEOUT)
        m = re.search(r'"LSD",\[\],\{"token":"([^"]+)"', r.text)
        if m:
            lsd = m.group(1)
            break
    if not lsd:
        raise RuntimeError("could not obtain Meta lsd token (page returned %s)" % r.status_code)
    r = session.post(
        "https://www.metacareers.com/graphql",
        data={
            "lsd": lsd,
            "fb_api_req_friendly_name": "CPJobSearchSourceQuery",
            "fb_api_caller_class": "RelayModern",
            "variables": json.dumps({"search_input": {
                "q": company.get("search", "intern"), "results_per_page": "HUNDRED"}}),
            "server_timestamps": "true",
            "doc_id": company.get("doc_id", "27807005005556827"),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded", "X-FB-LSD": lsd,
                 "Origin": "https://www.metacareers.com",
                 "Referer": "https://www.metacareers.com/jobs"},
        timeout=TIMEOUT)
    r.raise_for_status()
    d = r.json()
    if d.get("errors"):
        raise RuntimeError("Meta GraphQL error (doc_id may have rotated): %s"
                           % d["errors"][0].get("message", "?")[:200])
    jobs = (d.get("data", {}).get("job_search_with_featured_jobs")
            or d.get("data", {}).get("job_search_with_featured_jobs_v2") or {})
    out = []
    for j in jobs.get("all_jobs", []):
        out.append({
            "title": j.get("title", "").strip(),
            "url": f"https://www.metacareers.com/jobs/{j.get('id')}",
            "location": "; ".join(j.get("locations", [])[:3]),
            "posted_at": "",
            "description": " ".join(j.get("teams", []) + j.get("sub_teams", [])),
        })
    return out


ADAPTERS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "eightfold": fetch_eightfold,
    "amazon": fetch_amazon,
    "apple": fetch_apple,
    "google": fetch_google,
    "meta": fetch_meta,
}


def fetch_company(company):
    """Fetch raw postings for one company config entry."""
    fn = ADAPTERS.get(company.get("type"))
    if fn is None:
        raise RuntimeError(f"unknown adapter type: {company.get('type')!r}")
    session = make_session()
    try:
        return fn(company, session)
    finally:
        session.close()
