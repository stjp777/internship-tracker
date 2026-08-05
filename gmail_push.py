"""Personal Gmail → shared database pusher. RUNS LOCALLY ONLY.

Reads LinkedIn/Indeed job-alert emails from YOUR OWN Gmail inbox using
YOUR OWN OAuth credentials (credentials.json / token.json, which never
leave this machine), then pushes the parsed postings to the shared Turso
database so everyone benefits.

Which-emails-are-already-processed tracking stays in your LOCAL sqlite
db; only the extracted job postings (title/company/url) go to the shared
store. Requires TURSO_DATABASE_URL / TURSO_AUTH_TOKEN in the environment
(ask the shared-db owner for a token; keep it out of any repo).

Run it on your own schedule, e.g. every 20 min via Task Scheduler:
    python gmail_push.py
"""
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from tracker import db  # noqa: E402
from tracker.filters import PostingFilter  # noqa: E402
from tracker.gmail_source import fetch_alert_jobs, gmail_available  # noqa: E402


def main():
    with open(BASE / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    local_path = BASE / "config.local.yaml"
    if local_path.exists():
        with open(local_path, encoding="utf-8") as f:
            for k, v in (yaml.safe_load(f) or {}).items():
                cfg[k] = {**cfg[k], **v} if isinstance(cfg.get(k), dict) else v
    g = cfg.get("gmail", {})
    for key in ("credentials", "token"):
        if key in g:
            g[key] = str(BASE / g[key])

    if not gmail_available(cfg):
        print("gmail not configured (credentials.json missing or disabled)")
        return

    local = db.connect(str(BASE / "internships.db"))   # seen-email tracking only
    shared = db.connect_store()                        # postings go here
    pf = PostingFilter(cfg)
    try:
        jobs = fetch_alert_jobs(cfg, local)
        pushed = 0
        for j in jobs:
            cats, _ = pf.accept(j["title"])
            if not cats:
                continue
            verdict, _id = db.upsert_posting(
                shared, company=j["company"] or "(see posting)", title=j["title"],
                url=j["url"], source=j["provider"], categories=cats)
            if verdict == "new":
                pushed += 1
        print(f"parsed {len(jobs)} job(s) from alert emails, {pushed} new pushed to shared db")
    finally:
        local.close()
        shared.close()


if __name__ == "__main__":
    main()
