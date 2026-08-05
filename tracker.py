"""Internship Tracker CLI.

Commands:
  python tracker.py poll            one full pass (career pages + gmail) + notify
  python tracker.py poll --seed     same, but suppress notifications (first run)
  python tracker.py poll --company NVIDIA   poll a single company
  python tracker.py gmail           check gmail alerts only
  python tracker.py daemon          run forever on the configured schedule
  python tracker.py serve           start the dashboard (http://127.0.0.1:5717)
  python tracker.py add URL [--company X] [--title Y]   quick-add a posting
  python tracker.py list            print newest postings to the console
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from tracker import db  # noqa: E402
from tracker.poller import poll_career_pages, poll_gmail, flush_notifications, run_once  # noqa: E402


def load_cfg():
    with open(BASE / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # Personal overrides (Discord webhook, gmail toggles, …) live in
    # config.local.yaml, which is gitignored and never leaves this machine.
    local = BASE / "config.local.yaml"
    if local.exists():
        with open(local, encoding="utf-8") as f:
            overlay = yaml.safe_load(f) or {}
        for k, v in overlay.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    # resolve relative paths against the project directory
    for key in ("database",):
        cfg[key] = str(BASE / cfg.get(key, "internships.db"))
    g = cfg.get("gmail", {})
    for key in ("credentials", "token"):
        if key in g:
            g[key] = str(BASE / g[key])
    return cfg


def cmd_daemon(cfg):
    sch = cfg.get("schedule", {})
    career_min = int(sch.get("career_pages_minutes", 60))
    gmail_min = int(sch.get("gmail_minutes", 20))
    night_min = int(sch.get("night_minutes", 180))
    start_h = int(sch.get("active_start_hour", 7))
    end_h = int(sch.get("active_end_hour", 23))
    last_career = last_gmail = 0.0
    print(f"[daemon] career pages every {career_min} min, gmail every {gmail_min} min "
          f"(active {start_h:02d}:00-{end_h:02d}:00; every {night_min} min at night). Ctrl+C to stop.")
    while True:
        cfg = load_cfg()  # pick up config edits without restart
        now = time.time()
        hour = datetime.now().hour
        active = start_h <= hour < end_h
        c_int = (career_min if active else night_min) * 60
        g_int = (gmail_min if active else night_min) * 60
        conn = db.connect(cfg["database"])
        try:
            did = False
            if now - last_career >= c_int:
                poll_career_pages(cfg, conn)
                last_career = now
                did = True
            if now - last_gmail >= g_int:
                poll_gmail(cfg, conn)
                last_gmail = now
                did = True
            if did:
                flush_notifications(cfg, conn)
        finally:
            conn.close()
        time.sleep(30)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("poll", help="one full polling pass")
    sp.add_argument("--seed", action="store_true", help="suppress notifications")
    sp.add_argument("--company", help="poll only this company")
    sp.add_argument("--no-gmail", action="store_true")

    sub.add_parser("gmail", help="check gmail alerts only")
    sub.add_parser("daemon", help="run on schedule forever")
    sub.add_parser("serve", help="start the dashboard")
    sub.add_parser("list", help="print newest postings")

    ap = sub.add_parser("add", help="quick-add a posting URL")
    ap.add_argument("url")
    ap.add_argument("--company", default="(manual)")
    ap.add_argument("--title", default="")

    sub.add_parser("sync-users",
                   help="push users.yaml into the shared db (uses TURSO_* env if set)")

    args = p.parse_args()
    cfg = load_cfg()

    # One-time backfill for rows created before the categories column existed
    if args.cmd in ("poll", "serve", "list", "gmail"):
        from tracker.filters import PostingFilter
        _conn = db.connect(cfg["database"])
        n = db.backfill_categories(_conn, PostingFilter(cfg))
        if n:
            print(f"[migrate] classified {n} existing posting(s) into categories")
        _conn.close()

    if args.cmd == "poll":
        conn = db.connect(cfg["database"])
        run_once(cfg, conn, gmail=not args.no_gmail,
                 only_company=args.company, quiet_notify=args.seed)
        conn.close()
    elif args.cmd == "gmail":
        conn = db.connect(cfg["database"])
        poll_gmail(cfg, conn)
        flush_notifications(cfg, conn)
        conn.close()
    elif args.cmd == "daemon":
        cmd_daemon(cfg)
    elif args.cmd == "serve":
        from tracker.dashboard import create_app
        d = cfg.get("dashboard", {})
        create_app(cfg).run(host=d.get("host", "127.0.0.1"), port=int(d.get("port", 5717)))
    elif args.cmd == "add":
        conn = db.connect(cfg["database"])
        verdict, pid = db.upsert_posting(
            conn, company=args.company, title=args.title or args.url[:120],
            url=args.url, source="manual")
        db.mark_notified(conn, [pid])
        print(f"{verdict}: id={pid}")
        conn.close()
    elif args.cmd == "sync-users":
        users_file = BASE / "users.yaml"
        if not users_file.exists():
            print("users.yaml not found — copy users.example.yaml to users.yaml first")
            sys.exit(1)
        with open(users_file, encoding="utf-8") as f:
            spec = yaml.safe_load(f) or {}
        conn = db.connect_store(cfg["database"], turso=cfg.get("turso"))
        for u in spec.get("users", []):
            db.upsert_user(conn, u["name"], webhook=u.get("webhook", ""),
                           states=u.get("states"), categories=u.get("categories"),
                           active=u.get("active", True))
            print(f"synced user: {u['name']}")
        conn.close()
    elif args.cmd == "list":
        conn = db.connect(cfg["database"])
        rows = conn.execute(
            "SELECT * FROM postings ORDER BY first_seen DESC LIMIT 40").fetchall()
        for r in rows:
            print(f"[{r['status']:9}] {r['first_seen'][:16]} {r['company'][:18]:18} "
                  f"{r['title'][:60]:60} {r['url'][:70]}")
        conn.close()


if __name__ == "__main__":
    main()
