"""Per-user Discord notification fan-out for the shared/hosted mode.

After a central poll, each active user in the `users` table gets pinged
about the new postings that match *their* state/category preferences.
An empty preference list means "everything".
"""
import json

import requests

from . import db
from .locations import REMOTE, UNKNOWN, split_states


def _states_match(user_states, posting_states):
    if not user_states:
        return True
    # Remote/unknown-location postings are shown to everyone rather than
    # silently hidden from state-filtered users.
    if REMOTE in posting_states or UNKNOWN in posting_states:
        return True
    return bool(set(user_states) & set(posting_states))


def _cats_match(user_cats, posting_cats):
    if not user_cats:
        return True
    return bool(set(user_cats) & set(posting_cats))


def _post(webhook, content):
    r = requests.post(webhook, json={"content": content}, timeout=15)
    r.raise_for_status()


def fan_out(cfg, conn):
    """Send per-user Discord notifications for unnotified postings, then
    mark them notified. Returns number of postings processed."""
    rows = db.unnotified(conn)
    if not rows:
        return 0
    users = db.active_users(conn)
    cap = int(cfg.get("notifications", {}).get("max_individual_per_run", 8))

    for u in users:
        webhook = (u["webhook"] or "").strip()
        if not webhook:
            continue
        u_states = json.loads(u["states"] or "[]")
        u_cats = json.loads(u["categories"] or "[]")
        matched = [
            r for r in rows
            if _states_match(u_states, split_states(r["state"]))
            and _cats_match(u_cats, json.loads(r["categories"] or "[]") or ["general"])
        ]
        try:
            if len(matched) > cap:
                companies = {}
                for r in matched:
                    companies[r["company"]] = companies.get(r["company"], 0) + 1
                detail = ", ".join(f"{c}: {n}" for c, n in sorted(companies.items()))
                _post(webhook, f"**{len(matched)} new internship postings** (bulk)\n{detail}")
            else:
                for r in matched:
                    loc = f" ({r['location']})" if r["location"] else ""
                    cats = "/".join(json.loads(r["categories"] or "[]") or ["general"])
                    _post(webhook,
                          f"**New {cats} internship** — {r['company']}: {r['title']}{loc}\n{r['url']}")
            if matched:
                print(f"[fanout] {u['name']}: {len(matched)} notification(s)")
        except Exception as e:
            print(f"[fanout] {u['name']}: webhook failed: {e}")

    db.mark_notified(conn, [r["id"] for r in rows])
    return len(rows)
