"""Orchestrates one polling pass: fetch → filter → dedupe/store → notify."""
import time
import traceback

from . import db
from .adapters import fetch_company
from .filters import PostingFilter
from .gmail_source import fetch_alert_jobs, gmail_available
from .notify import notify_new_postings


def _fetch_variants(company):
    """Expand a company entry into per-category fetch jobs.

    `search:` may be a plain string (one fetch, no hint) or a mapping of
    category -> search-term / {q, category(site facet), ...} for adapters
    whose remote API narrows results by the search term.
    """
    search = company.get("search")
    if not isinstance(search, dict):
        return [(None, company)]
    variants = []
    for cat, spec in search.items():
        v = dict(company)
        v.pop("category", None)
        if isinstance(spec, dict):
            v["search"] = spec.get("q", "intern")
            for k, val in spec.items():
                if k != "q":
                    v[k] = val
        else:
            v["search"] = spec
        variants.append((cat, v))
    return variants


def poll_career_pages(cfg, conn, only_company=None):
    pf = PostingFilter(cfg)
    delay = cfg.get("schedule", {}).get("inter_company_delay_seconds", 5)
    new_count = 0
    for company in cfg.get("companies", []):
        name = company["name"]
        if only_company and name.lower() != only_company.lower():
            continue
        try:
            raw, seen_urls = [], set()
            for hint, variant in _fetch_variants(company):
                for j in fetch_company(variant):
                    if j["url"] in seen_urls:
                        continue
                    seen_urls.add(j["url"])
                    j["category_hint"] = hint
                    raw.append(j)
            kept = 0
            for j in raw:
                cats, _reason = pf.accept(j["title"], j.get("description", ""),
                                          category_hint=j.get("category_hint"))
                if not cats:
                    continue
                if not pf.location_ok(j.get("location", ""),
                                      source_us_filtered=company.get("us_filtered", False)):
                    continue
                kept += 1
                verdict, _id = db.upsert_posting(
                    conn, company=name, title=j["title"], url=j["url"],
                    source="career_page", location=j.get("location", ""),
                    posted_at=j.get("posted_at", ""), categories=cats)
                if verdict == "new":
                    new_count += 1
            db.record_health(conn, f"career:{name}", True)
            print(f"[career] {name}: {len(raw)} fetched, {kept} matched filters")
        except Exception as e:
            db.record_health(conn, f"career:{name}", False, str(e)[:300])
            print(f"[career] {name}: ERROR {e}")
            traceback.print_exc()
        time.sleep(delay)
    return new_count


def poll_gmail(cfg, conn):
    if not gmail_available(cfg):
        print("[gmail] skipped (no credentials.json or disabled in config)")
        return 0
    pf = PostingFilter(cfg)
    try:
        jobs = fetch_alert_jobs(cfg, conn)
        db.record_health(conn, "gmail", True)
    except Exception as e:
        db.record_health(conn, "gmail", False, str(e)[:300])
        print(f"[gmail] ERROR {e}")
        return 0
    new_count = 0
    for j in jobs:
        cats, _ = pf.accept(j["title"])
        if not cats:
            continue
        verdict, _id = db.upsert_posting(
            conn, company=j["company"] or "(see posting)", title=j["title"],
            url=j["url"], source=j["provider"], categories=cats)
        if verdict == "new":
            new_count += 1
    print(f"[gmail] {len(jobs)} jobs parsed from alerts, {new_count} new")
    return new_count


def flush_notifications(cfg, conn):
    rows = db.unnotified(conn)
    if rows:
        notify_new_postings(cfg, rows)
        db.mark_notified(conn, [r["id"] for r in rows])
        print(f"[notify] sent {len(rows)} notification(s)")


def run_once(cfg, conn, career=True, gmail=True, only_company=None, quiet_notify=False):
    total = 0
    if career:
        total += poll_career_pages(cfg, conn, only_company=only_company)
    if gmail:
        total += poll_gmail(cfg, conn)
    if quiet_notify:
        # mark everything notified without sending (used for the first seed run)
        rows = db.unnotified(conn)
        db.mark_notified(conn, [r["id"] for r in rows])
        print(f"[notify] suppressed {len(rows)} notification(s) (seed run)")
    else:
        flush_notifications(cfg, conn)
    return total
