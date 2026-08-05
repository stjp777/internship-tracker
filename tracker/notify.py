"""Per-posting notifications: Windows toast and/or Discord webhook."""
import json

import requests


def _toast(title, body, url=None):
    try:
        from windows_toasts import Toast, WindowsToaster
        toaster = WindowsToaster("Internship Tracker")
        t = Toast()
        t.text_fields = [title, body]
        if url:
            t.launch_action = url
        toaster.show_toast(t)
        return True
    except Exception as e:
        print(f"  [notify] desktop toast failed: {e}")
        return False


def _discord(webhook_url, content):
    try:
        r = requests.post(webhook_url, json={"content": content}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  [notify] discord webhook failed: {e}")
        return False


def notify_new_postings(cfg, rows):
    """rows: list of sqlite rows for postings not yet notified.
    Sends one notification per posting, unless the batch is huge
    (first run) in which case it sends a single summary."""
    if not rows:
        return
    n = cfg.get("notifications", {})
    desktop = n.get("desktop", True)
    webhook = (n.get("discord_webhook") or "").strip()
    cap = int(n.get("max_individual_per_run", 8))

    if len(rows) > cap:
        summary = f"{len(rows)} new internship postings found (bulk load)"
        companies = {}
        for r in rows:
            companies[r["company"]] = companies.get(r["company"], 0) + 1
        detail = ", ".join(f"{c}: {k}" for c, k in sorted(companies.items()))
        if desktop:
            _toast(summary, detail)
        if webhook:
            _discord(webhook, f"**{summary}**\n{detail}\nOpen the dashboard to review them.")
        return

    for r in rows:
        title = f"{r['company']}: {r['title']}"
        body = " | ".join(x for x in [r["location"], json.loads(r["sources"])[0]] if x)
        if desktop:
            _toast(title, body or "new internship posting", r["url"])
        if webhook:
            loc = f" ({r['location']})" if r["location"] else ""
            _discord(webhook, f"**New internship** — {r['company']}: {r['title']}{loc}\n{r['url']}")
