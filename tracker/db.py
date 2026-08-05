"""SQLite storage with cross-source deduplication."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .locations import states_str

SCHEMA = """
CREATE TABLE IF NOT EXISTS postings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key  TEXT UNIQUE NOT NULL,
    company     TEXT NOT NULL,
    title       TEXT NOT NULL,
    url         TEXT NOT NULL,
    sources     TEXT NOT NULL,          -- JSON list, e.g. ["career_page","linkedin"]
    location    TEXT DEFAULT '',
    posted_at   TEXT DEFAULT '',        -- as reported by the source, if any
    first_seen  TEXT NOT NULL,          -- ISO timestamp we first saw it
    deadline    TEXT DEFAULT '',
    status      TEXT DEFAULT 'New',     -- New / Viewed / Applied / Dismissed
    notified    INTEGER DEFAULT 0,
    notes       TEXT DEFAULT '',
    state       TEXT DEFAULT '',        -- comma-joined codes: "CA,WA" / "REMOTE" / "UNKNOWN"
    categories  TEXT DEFAULT '[]'       -- JSON list, e.g. ["software"]
);
CREATE INDEX IF NOT EXISTS idx_postings_first_seen ON postings(first_seen DESC);
CREATE TABLE IF NOT EXISTS seen_emails (
    gmail_id     TEXT PRIMARY KEY,
    processed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_health (
    source     TEXT PRIMARY KEY,
    last_ok    TEXT DEFAULT '',
    last_error TEXT DEFAULT '',
    error_msg  TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
    name       TEXT PRIMARY KEY,
    webhook    TEXT DEFAULT '',        -- Discord webhook URL
    states     TEXT DEFAULT '[]',      -- JSON list of state codes; [] = all
    categories TEXT DEFAULT '[]',      -- JSON list of categories;  [] = all
    active     INTEGER DEFAULT 1,
    -- Highest posting id successfully delivered to this user. Advances only
    -- after their webhook accepts, so a failed send is retried next run
    -- instead of being lost.
    last_posting_id INTEGER DEFAULT 0
);
"""


def connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def connect_store(db_path=None, turso=None):
    """Local SQLite by default; the shared Turso database when credentials
    are available — from TURSO_DATABASE_URL / TURSO_AUTH_TOKEN env vars or
    a {url, token} dict (e.g. the `turso:` block of config.local.yaml).
    Both speak the same interface, so callers don't care which they get."""
    import os
    url = os.environ.get("TURSO_DATABASE_URL", "").strip()
    token = os.environ.get("TURSO_AUTH_TOKEN", "").strip()
    if not (url and token) and turso:
        url = (turso.get("url") or "").strip()
        token = (turso.get("token") or "").strip()
    if url and token:
        from .turso_store import TursoConn
        conn = TursoConn(url, token)
        conn.executescript(SCHEMA)
        _migrate(conn)
        return conn
    if not db_path:
        raise ValueError("no local db path given and no Turso credentials found")
    return connect(db_path)


def _migrate(conn):
    """Additive, idempotent migrations for pre-existing databases."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(postings)").fetchall()}
    if "state" not in cols:
        conn.execute("ALTER TABLE postings ADD COLUMN state TEXT DEFAULT ''")
    if "categories" not in cols:
        conn.execute("ALTER TABLE postings ADD COLUMN categories TEXT DEFAULT '[]'")
    ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if ucols and "last_posting_id" not in ucols:
        # Existing users start caught-up rather than being flooded with the
        # whole backlog on the next run.
        conn.execute("ALTER TABLE users ADD COLUMN last_posting_id INTEGER DEFAULT 0")
        conn.execute("UPDATE users SET last_posting_id ="
                     " (SELECT COALESCE(MAX(id), 0) FROM postings)")
    # Backfill state for rows created before the column existed
    stale = conn.execute(
        "SELECT id, location FROM postings WHERE state = '' OR state IS NULL").fetchall()
    for r in stale:
        conn.execute("UPDATE postings SET state = ? WHERE id = ?",
                     (states_str(r["location"]), r["id"]))
    conn.commit()


def _norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (text or "").lower())).strip()


def dedupe_key(company, title, url=""):
    """Same company + same normalized title collapses to one entry,
    regardless of which source reported it."""
    key = f"{_norm(company)}|{_norm(title)}"
    if key != "|":
        return key
    return f"url|{(url or '').split('?')[0].rstrip('/').lower()}"


def upsert_posting(conn, *, company, title, url, source, location="", posted_at="",
                   deadline="", categories=None):
    """Insert a posting or merge a new source into an existing one.

    Returns "new" if this is a never-before-seen posting, "merged" if it
    matched an existing entry, and the row id.
    """
    key = dedupe_key(company, title, url)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    row = conn.execute("SELECT * FROM postings WHERE dedupe_key = ?", (key,)).fetchone()
    if row is None:
        cur = conn.execute(
            "INSERT INTO postings (dedupe_key, company, title, url, sources, location,"
            " posted_at, first_seen, deadline, state, categories) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (key, company, title, url, json.dumps([source]), location, posted_at, now,
             deadline, states_str(location), json.dumps(sorted(categories or []))),
        )
        conn.commit()
        return "new", cur.lastrowid
    sources = json.loads(row["sources"])
    changed = False
    if source not in sources:
        sources.append(source)
        changed = True
    updates, params = [], []
    if changed:
        updates.append("sources = ?")
        params.append(json.dumps(sources))
    merged_cats = sorted(set(json.loads(row["categories"] or "[]")) | set(categories or []))
    if merged_cats != sorted(json.loads(row["categories"] or "[]")):
        updates.append("categories = ?")
        params.append(json.dumps(merged_cats))
    # A source with real location info wins over an earlier unknown
    if location and (row["state"] in ("", "UNKNOWN") or not row["location"]):
        new_state = states_str(location)
        if new_state != "UNKNOWN" and new_state != row["state"]:
            updates.append("location = ?")
            params.append(location)
            updates.append("state = ?")
            params.append(new_state)
    # Career-page URLs are canonical; prefer them over email-tracking links.
    if source == "career_page" and "career_page" not in json.loads(row["sources"]):
        updates.append("url = ?")
        params.append(url)
    if deadline and not row["deadline"]:
        updates.append("deadline = ?")
        params.append(deadline)
    if updates:
        params.append(row["id"])
        conn.execute(f"UPDATE postings SET {', '.join(updates)} WHERE id = ?", params)
        conn.commit()
    return "merged", row["id"]


def backfill_categories(conn, pf):
    """Classify rows created before the categories column existed."""
    rows = conn.execute(
        "SELECT id, title FROM postings WHERE categories = '[]' OR categories IS NULL").fetchall()
    for r in rows:
        cats, _ = pf.accept(r["title"])
        conn.execute("UPDATE postings SET categories = ? WHERE id = ?",
                     (json.dumps(cats or ["general"]), r["id"]))
    if rows:
        conn.commit()
    return len(rows)


def unnotified(conn):
    return conn.execute(
        "SELECT * FROM postings WHERE notified = 0 ORDER BY first_seen ASC").fetchall()


def mark_notified(conn, ids):
    conn.executemany("UPDATE postings SET notified = 1 WHERE id = ?", [(i,) for i in ids])
    conn.commit()


def set_status(conn, posting_id, status):
    conn.execute("UPDATE postings SET status = ? WHERE id = ?", (status, posting_id))
    conn.commit()


def email_seen(conn, gmail_id):
    return conn.execute(
        "SELECT 1 FROM seen_emails WHERE gmail_id = ?", (gmail_id,)).fetchone() is not None


def mark_email_seen(conn, gmail_id):
    conn.execute(
        "INSERT OR IGNORE INTO seen_emails (gmail_id, processed_at) VALUES (?, ?)",
        (gmail_id, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    conn.commit()


def upsert_user(conn, name, webhook="", states=None, categories=None, active=True):
    """Create or update a user. New users start caught-up (they are not
    notified about the existing backlog); re-syncing preferences never
    rewinds or skips their delivery watermark."""
    conn.execute(
        "INSERT INTO users (name, webhook, states, categories, active, last_posting_id)"
        " VALUES (?,?,?,?,?, (SELECT COALESCE(MAX(id), 0) FROM postings))"
        " ON CONFLICT(name) DO UPDATE SET webhook=?, states=?, categories=?, active=?",
        (name, webhook, json.dumps(states or []), json.dumps(categories or []), int(active),
         webhook, json.dumps(states or []), json.dumps(categories or []), int(active)))
    conn.commit()


def active_users(conn):
    return conn.execute("SELECT * FROM users WHERE active = 1").fetchall()


def postings_after(conn, posting_id):
    """Postings newer than a user's delivery watermark."""
    return conn.execute(
        "SELECT * FROM postings WHERE id > ? ORDER BY id ASC", (posting_id,)).fetchall()


def set_user_watermark(conn, name, posting_id):
    conn.execute("UPDATE users SET last_posting_id = ? WHERE name = ?", (posting_id, name))
    conn.commit()


def catch_up_all_users(conn):
    """Mark every user current without notifying — used by seed runs."""
    conn.execute("UPDATE users SET last_posting_id ="
                 " (SELECT COALESCE(MAX(id), 0) FROM postings)")
    conn.commit()


def record_health(conn, source, ok, error_msg=""):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if ok:
        conn.execute(
            "INSERT INTO source_health (source, last_ok) VALUES (?, ?)"
            " ON CONFLICT(source) DO UPDATE SET last_ok = ?, error_msg = ''",
            (source, now, now))
    else:
        conn.execute(
            "INSERT INTO source_health (source, last_error, error_msg) VALUES (?, ?, ?)"
            " ON CONFLICT(source) DO UPDATE SET last_error = ?, error_msg = ?",
            (source, now, error_msg, now, error_msg))
    conn.commit()
