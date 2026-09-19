"""Per-user Discord notification fan-out for the shared/hosted mode.

After a central poll, each active user in the `users` table gets pinged
about postings newer than their delivery watermark that match *their*
state/category preferences. An empty preference list means "everything".

Delivery is tracked per user, not globally: a user whose webhook fails
keeps their old watermark and is retried on the next run, and one user's
outage never swallows another's notifications.
"""
import json
import re
import time

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


DISCORD_MAX_CHARS = 1900  # Discord rejects content over 2000 with HTTP 400


def discord_escape(text):
    """Neutralise Discord markdown in untrusted posting text, so a title like
    "[careers.google.com](https://evil.example)" can't render as a masked link."""
    one_line = re.sub(r"[\r\n]+", " ", str(text or ""))  # no forged extra lines
    return re.sub(r"([\\*_`~|>\[\]()<])", r"\\\1", one_line)


def discord_payload(content):
    # Posting text is untrusted; without this, a job title containing
    # "@everyone" would ping the whole server.
    if len(content) > DISCORD_MAX_CHARS:
        content = content[:DISCORD_MAX_CHARS - 1] + "…"
    return {"content": content, "allowed_mentions": {"parse": []}}


def _post(webhook, content):
    """POST to Discord, honouring 429 rate-limit backoff.

    Raises only messages that are safe to print: a webhook url is a bearer
    credential, and requests' own exceptions embed the full url, which would
    put it in this repo's public Actions log.
    """
    for attempt in range(3):
        try:
            r = requests.post(webhook, json=discord_payload(content), timeout=15)
        except requests.RequestException as e:
            raise RuntimeError(f"discord request failed ({type(e).__name__})") from None
        if r.status_code == 429:
            wait = 1.0
            try:
                wait = float(r.json().get("retry_after", 1.0))
            except Exception:
                pass
            time.sleep(min(wait, 10) + 0.1)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"discord returned HTTP {r.status_code}")
        return
    raise RuntimeError("discord rate-limited after 3 attempts")


def fan_out(cfg, conn):
    """Notify each active user about postings past their watermark.

    Returns the number of postings that were new to at least one user."""
    users = db.active_users(conn)
    cap = int(cfg.get("notifications", {}).get("max_individual_per_run", 8))
    touched = set()

    for u in users:
        webhook = (u["webhook"] or "").strip()
        if not webhook:
            continue
        watermark = u["last_posting_id"] or 0
        rows = db.postings_after(conn, watermark)
        if not rows:
            continue
        u_states = json.loads(u["states"] or "[]")
        u_cats = json.loads(u["categories"] or "[]")
        matched = [
            r for r in rows
            if _states_match(u_states, split_states(r["state"]))
            and _cats_match(u_cats, json.loads(r["categories"] or "[]") or ["general"])
        ]
        highest = max(r["id"] for r in rows)
        try:
            if len(matched) > cap:
                companies = {}
                for r in matched:
                    companies[r["company"]] = companies.get(r["company"], 0) + 1
                detail = ", ".join(f"{discord_escape(c)}: {n}"
                                   for c, n in sorted(companies.items()))
                _post(webhook, f"**{len(matched)} new internship postings** (bulk)\n{detail}")
            else:
                for r in matched:
                    loc = f" ({discord_escape(r['location'])})" if r["location"] else ""
                    cats = "/".join(json.loads(r["categories"] or "[]") or ["general"])
                    _post(webhook,
                          f"**New {cats} internship** — {discord_escape(r['company'])}: "
                          f"{discord_escape(r['title'])}{loc}\n{r['url']}")
            # Only advance past postings we actually delivered; a failed
            # webhook leaves the watermark alone so the next run retries.
            db.set_user_watermark(conn, u["name"], highest)
            touched.update(r["id"] for r in matched)
            if matched:
                print(f"[fanout] {u['name']}: {len(matched)} notification(s)")
        except Exception as e:
            print(f"[fanout] {u['name']}: webhook failed, will retry next run: {e}")

    # Keep the global flag in sync so a local `poll` doesn't re-announce
    # what the cloud already sent.
    unsent = db.unnotified(conn)
    if unsent:
        db.mark_notified(conn, [r["id"] for r in unsent])
    return len(touched)
