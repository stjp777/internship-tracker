"""Entrypoint for the scheduled GitHub Actions run (shared/hosted mode).

Career pages only — no Gmail, no personal data. Writes to the shared
Turso database (TURSO_DATABASE_URL / TURSO_AUTH_TOKEN env vars), sends
per-user Discord notifications, and renders the static dashboard.

Also works from any machine for testing:
    set TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... && python cloud_poll.py
"""
import sys
from pathlib import Path

import yaml

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from tracker import db  # noqa: E402
from tracker.fanout import fan_out  # noqa: E402
from tracker.poller import poll_career_pages  # noqa: E402
from tracker.render_static import render  # noqa: E402


def main():
    with open(BASE / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    conn = db.connect_store()  # requires TURSO_* env vars
    try:
        seed = "--seed" in sys.argv
        new = poll_career_pages(cfg, conn)
        print(f"[cloud] {new} new posting(s)")
        if seed:
            rows = db.unnotified(conn)
            db.mark_notified(conn, [r["id"] for r in rows])
            print(f"[cloud] seed run: suppressed {len(rows)} notification(s)")
        else:
            fan_out(cfg, conn)
        site = BASE / "site"
        site.mkdir(exist_ok=True)
        n = render(conn, site / "index.html")
        print(f"[cloud] rendered static dashboard with {n} posting(s)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
