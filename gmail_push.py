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

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from tracker import db  # noqa: E402
from tracker.config import load_config  # noqa: E402
from tracker.filters import PostingFilter  # noqa: E402
from tracker.gmail_source import fetch_alert_jobs, gmail_available  # noqa: E402


def main():
    cfg = load_config(BASE)
    if not gmail_available(cfg):
        print("gmail not configured (credentials.json missing or disabled)")
        return

    local = db.connect(str(BASE / "internships.db"))            # seen-email tracking only
    shared = db.connect_store(turso=cfg.get("turso"))           # postings go here
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
