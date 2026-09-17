"""Offline unit tests: python -m unittest discover -s tests

No network, no credentials — safe to run anywhere, including CI.
"""
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import db  # noqa: E402
from tracker.fanout import _cats_match, _states_match  # noqa: E402
from tracker.filters import PostingFilter  # noqa: E402
from tracker.gmail_source import extract_jobs_from_html  # noqa: E402
import requests  # noqa: E402

from tracker.http_util import _parse_robots, _robots_allowed, get_with_backoff  # noqa: E402
from tracker.locations import state_tokens  # noqa: E402
from tracker.poller import poll_career_pages  # noqa: E402
from tracker.render_static import render  # noqa: E402

CFG = {
    "filters": {
        "base_include_title": r"\bintern(ship)?s?\b|\bco-?op\b|student researcher",
        "exclude_title": r"\b(phd|senior|staff|principal|director)\b",
        "term": r"\b(2026|2027|summer|winter|fall)\b",
        "keep_if_no_term_info": True,
        "categories": [
            {"name": "software", "include_title": r"\b(software|swe|machine learning)\b"},
            {"name": "finance", "include_title": r"\b(finance|financial|tax|accounting)\b"},
        ],
        "location": {"us_only": True, "preferred_states": ["CA"]},
    }
}


class TestLocations(unittest.TestCase):
    def test_formats_seen_in_the_wild(self):
        cases = {
            "San Francisco, CA": ["CA"],
            "Mountain View, California": ["CA"],
            "US, CA, Santa Clara": ["CA"],
            "United States, Washington, Redmond": ["WA"],
            "Bellevue, WA; Menlo Park, CA; Seattle, WA": ["CA", "WA"],
            "Cupertino": ["CA"],
            "Remote": ["REMOTE"],
            "Remote - California": ["CA", "REMOTE"],
            "Washington, DC": ["DC"],
            "": ["UNKNOWN"],
            "3 Locations": ["UNKNOWN"],
            "United States": ["UNKNOWN"],
        }
        for loc, want in cases.items():
            self.assertEqual(state_tokens(loc), want, loc)

    def test_lowercase_prose_is_not_a_state(self):
        # "in", "or", "me" are state codes only when uppercase
        self.assertEqual(state_tokens("Media Design office"), ["UNKNOWN"])


class TestFilters(unittest.TestCase):
    def setUp(self):
        self.pf = PostingFilter(CFG)

    def test_categories(self):
        cats, _ = self.pf.accept("Software Engineer Intern, Summer 2027")
        self.assertEqual(cats, ["software"])
        cats, _ = self.pf.accept("2027 Tax Intern (Summer Internship)")
        self.assertEqual(cats, ["finance"])

    def test_multi_category(self):
        cats, _ = self.pf.accept("Software Intern, Financial Systems - Summer 2027")
        self.assertEqual(cats, ["finance", "software"])

    def test_uncategorized_is_general(self):
        cats, _ = self.pf.accept("Legal Internships 2027")
        self.assertEqual(cats, ["general"])

    def test_rejections(self):
        self.assertEqual(self.pf.accept("Senior Software Engineer")[0], [])
        self.assertEqual(self.pf.accept("Research Intern - PhD, Fall 2026")[0], [])

    def test_category_hint_from_source_facet(self):
        # Amazon's finance facet vouches for an internship title our regex
        # can't classify on its own.
        cats, _ = self.pf.accept("2027 FLDP Rotational Program Internship",
                                 category_hint="finance")
        self.assertEqual(cats, ["finance"])

    def test_gate_still_applies_with_a_hint(self):
        # A hint must not smuggle in a non-internship posting.
        cats, _ = self.pf.accept("Rotational Program Analyst", category_hint="finance")
        self.assertEqual(cats, [])

    def test_us_only_location(self):
        self.assertTrue(self.pf.location_ok("Seattle, WA"))
        self.assertTrue(self.pf.location_ok("Remote, US"))
        self.assertTrue(self.pf.location_ok(""))  # unknown is kept
        self.assertFalse(self.pf.location_ok("Bengaluru"))
        self.assertFalse(self.pf.location_ok("London, UK"))

    def test_preferred_state(self):
        self.assertTrue(self.pf.is_preferred_state("CA,WA"))
        self.assertFalse(self.pf.is_preferred_state("WA"))


class TestRobots(unittest.TestCase):
    def test_shared_user_agent_group(self):
        # Google's real shape: "*" and "Yandex" share one block, then
        # Yandex gets extra rules of its own.
        txt = ("User-agent: *\nUser-agent: Yandex\n"
               "Disallow: /search\nAllow: /search/about\n\n"
               "User-agent: Yandex\nDisallow: /careers\n")
        g = _parse_robots(txt)
        self.assertFalse(_robots_allowed(g, "*", "/search"))
        self.assertTrue(_robots_allowed(g, "*", "/search/about"))
        self.assertTrue(_robots_allowed(g, "*", "/careers"))
        self.assertFalse(_robots_allowed(g, "yandex", "/careers"))

    def test_wildcards_and_anchors(self):
        g = _parse_robots("User-agent: *\nDisallow: /*.pdf$\nDisallow: /tmp*")
        self.assertFalse(_robots_allowed(g, "*", "/doc.pdf"))
        self.assertTrue(_robots_allowed(g, "*", "/doc.pdfx"))
        self.assertFalse(_robots_allowed(g, "*", "/tmp/x"))
        self.assertTrue(_robots_allowed(g, "*", "/temp"))

    def test_comments_ignored(self):
        g = _parse_robots("User-agent: *\n# Disallow: /everything\nDisallow: /x")
        self.assertTrue(_robots_allowed(g, "*", "/everything"))
        self.assertFalse(_robots_allowed(g, "*", "/x"))


class TestFanoutMatching(unittest.TestCase):
    def test_states(self):
        self.assertTrue(_states_match([], ["CA"]))            # no filter = all
        self.assertTrue(_states_match(["CA"], ["CA", "WA"]))
        self.assertFalse(_states_match(["NY"], ["CA", "WA"]))
        self.assertTrue(_states_match(["NY"], ["REMOTE"]))    # remote goes to everyone
        self.assertTrue(_states_match(["NY"], ["UNKNOWN"]))

    def test_categories(self):
        self.assertTrue(_cats_match([], ["software"]))
        self.assertFalse(_cats_match(["finance"], ["software"]))
        self.assertTrue(_cats_match(["finance"], ["finance", "software"]))


class TestDb(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def test_dedupe_across_sources(self):
        v1, id1 = db.upsert_posting(
            self.conn, company="Acme", title="Software Intern 2027",
            url="https://acme.com/j/1", source="career_page",
            location="San Jose, CA", categories=["software"])
        v2, id2 = db.upsert_posting(
            self.conn, company="Acme", title="Software  Intern 2027!",
            url="https://li.com/tracked", source="linkedin", categories=["software"])
        self.assertEqual((v1, v2), ("new", "merged"))
        self.assertEqual(id1, id2)
        row = self.conn.execute("SELECT * FROM postings WHERE id=?", (id1,)).fetchone()
        self.assertEqual(json.loads(row["sources"]), ["career_page", "linkedin"])
        # career-page URL stays canonical, not the email tracking link
        self.assertEqual(row["url"], "https://acme.com/j/1")

    def test_state_populated_on_insert(self):
        _, pid = db.upsert_posting(
            self.conn, company="Acme", title="Intern", url="u",
            source="career_page", location="Menlo Park, CA")
        row = self.conn.execute("SELECT state FROM postings WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["state"], "CA")

    def test_known_location_replaces_unknown(self):
        _, pid = db.upsert_posting(self.conn, company="Acme", title="Intern",
                                   url="u", source="linkedin", location="")
        db.upsert_posting(self.conn, company="Acme", title="Intern", url="u2",
                          source="career_page", location="Austin, TX")
        row = self.conn.execute("SELECT state FROM postings WHERE id=?", (pid,)).fetchone()
        self.assertEqual(row["state"], "TX")

    def test_new_user_starts_caught_up(self):
        db.upsert_posting(self.conn, company="Acme", title="Intern A", url="a",
                          source="career_page")
        db.upsert_user(self.conn, "friend", webhook="https://example.invalid/hook")
        u = db.active_users(self.conn)[0]
        self.assertGreater(u["last_posting_id"], 0)
        self.assertEqual(db.postings_after(self.conn, u["last_posting_id"]), [])

    def test_resync_preserves_watermark(self):
        db.upsert_posting(self.conn, company="Acme", title="Intern A", url="a",
                          source="career_page")
        db.upsert_user(self.conn, "friend", webhook="w", states=["CA"])
        before = db.active_users(self.conn)[0]["last_posting_id"]
        db.upsert_posting(self.conn, company="Acme", title="Intern B", url="b",
                          source="career_page")
        db.upsert_user(self.conn, "friend", webhook="w", states=["NY"])  # edit prefs
        after = db.active_users(self.conn)[0]
        self.assertEqual(after["last_posting_id"], before)   # not fast-forwarded
        self.assertEqual(json.loads(after["states"]), ["NY"])  # prefs did update
        self.assertEqual(len(db.postings_after(self.conn, before)), 1)

    def test_watermark_advances(self):
        _, pid = db.upsert_posting(self.conn, company="Acme", title="Intern A",
                                   url="a", source="career_page")
        db.set_user_watermark(self.conn, "nobody", pid)  # no-op on missing user
        db.upsert_user(self.conn, "friend", webhook="w")
        db.set_user_watermark(self.conn, "friend", pid)
        self.assertEqual(db.active_users(self.conn)[0]["last_posting_id"], pid)


def _job(title, url):
    return {"title": title, "url": url, "location": "San Jose, CA",
            "posted_at": "", "description": ""}


class TestRemoval(unittest.TestCase):
    """Closed postings get hidden — but never on a bad fetch, and never the
    user's own Applied record."""

    OLD = "2020-01-01T00:00:00+00:00"

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        self.cfg = dict(CFG, companies=[{"name": "Acme", "type": "greenhouse", "board": "acme"}],
                        schedule={"inter_company_delay_seconds": 0},
                        removal={"career_grace_days": 3, "email_max_age_days": 30})

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    def poll(self, jobs=None, error=None):
        fake = mock.Mock(side_effect=error) if error else mock.Mock(return_value=jobs)
        with mock.patch("tracker.poller.fetch_company", fake), \
                mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            poll_career_pages(self.cfg, self.conn)

    def age_all(self):
        self.conn.execute("UPDATE postings SET last_seen = ?, first_seen = ?", (self.OLD, self.OLD))
        self.conn.commit()

    def visible(self):
        return {r["title"] for r in self.conn.execute(
            "SELECT title FROM postings WHERE removed_at = ''").fetchall()}

    A = _job("Software Intern 2027", "https://acme.com/a")
    B = _job("SWE Intern Summer 2027", "https://acme.com/b")

    def test_posting_dropped_from_board_is_hidden(self):
        self.poll([self.A, self.B])
        self.age_all()
        self.poll([self.A])
        self.assertEqual(self.visible(), {self.A["title"]})

    def test_still_listed_posting_is_refreshed_not_hidden(self):
        self.poll([self.A])
        self.age_all()
        self.poll([self.A])
        row = self.conn.execute("SELECT last_seen FROM postings").fetchone()
        self.assertGreater(row["last_seen"], self.OLD)

    def test_failed_fetch_hides_nothing(self):
        self.poll([self.A, self.B])
        self.age_all()
        self.poll(error=RuntimeError("429 Too Many Requests"))
        self.assertEqual(len(self.visible()), 2)

    def test_empty_fetch_hides_nothing(self):
        self.poll([self.A, self.B])
        self.age_all()
        self.poll([])
        self.assertEqual(len(self.visible()), 2)

    def test_within_grace_period_is_kept(self):
        self.poll([self.A, self.B])
        self.poll([self.A])  # B only just went missing
        self.assertEqual(len(self.visible()), 2)

    def test_applied_posting_is_never_hidden(self):
        self.poll([self.A, self.B])
        self.conn.execute("UPDATE postings SET status = 'Applied' WHERE title = ?",
                          (self.B["title"],))
        self.age_all()
        self.poll([self.A])
        self.assertIn(self.B["title"], self.visible())

    def test_relisted_posting_comes_back_without_renotifying(self):
        self.poll([self.A, self.B])
        self.age_all()
        self.poll([self.A])
        verdict, _ = db.upsert_posting(self.conn, company="Acme", title=self.B["title"],
                                       url=self.B["url"], source="career_page")
        self.assertEqual(verdict, "merged")
        self.assertIn(self.B["title"], self.visible())

    def test_email_postings_age_out_but_manual_adds_stay(self):
        for title, src in [("Email Intern", "linkedin"), ("Manual Intern", "manual")]:
            db.upsert_posting(self.conn, company="Other", title=title, url="https://x.com/" + src,
                              source=src)
        # an emailed posting the career page also confirms follows the career rule
        db.upsert_posting(self.conn, company="Acme", title=self.A["title"],
                          url="https://li.com/1", source="linkedin")
        self.poll([self.A])
        self.age_all()
        self.poll([self.A])
        self.assertEqual(self.visible(), {"Manual Intern", self.A["title"]})

    def test_hidden_postings_are_not_notified(self):
        self.poll([self.A, self.B])
        self.age_all()
        self.poll([self.A])
        titles = {r["title"] for r in db.postings_after(self.conn, 0)}
        self.assertEqual(titles, {self.A["title"]})

    def test_migration_backfills_last_seen_to_now(self):
        old = sqlite3.connect(self.path + ".old")
        old.execute("CREATE TABLE postings (id INTEGER PRIMARY KEY, dedupe_key TEXT UNIQUE,"
                    " company TEXT, title TEXT, url TEXT, sources TEXT, location TEXT DEFAULT '',"
                    " first_seen TEXT, status TEXT DEFAULT 'New', state TEXT DEFAULT 'CA',"
                    " categories TEXT DEFAULT '[]')")
        old.execute("INSERT INTO postings (dedupe_key, company, title, url, sources, first_seen)"
                    " VALUES ('k', 'Acme', 'Intern', 'u', '[\"career_page\"]', ?)", (self.OLD,))
        old.commit()
        old.close()
        try:
            conn = db.connect(self.path + ".old")
            row = conn.execute("SELECT last_seen, removed_at FROM postings").fetchone()
            conn.close()
        finally:
            os.unlink(self.path + ".old")
        # backfilling to first_seen would make the first cleanup empty the feed
        self.assertGreater(row["last_seen"], self.OLD)
        self.assertEqual(row["removed_at"], "")

    def test_row_written_without_last_seen_is_not_treated_as_stale(self):
        # older deployed code inserts rows without last_seen until it's updated
        self.poll([self.A, self.B])
        self.conn.execute("UPDATE postings SET last_seen = ''")
        self.conn.commit()
        self.conn.close()
        self.conn = db.connect(self.path)  # reconnecting runs the migration
        self.poll([self.A])
        self.assertEqual(len(self.visible()), 2)


class TestReviewFixes(unittest.TestCase):
    """Findings from the second external security review (SEC-01..07)."""

    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close()
        os.unlink(self.path)

    # SEC-04
    def test_discord_messages_cannot_ping_everyone(self):
        from tracker import fanout, notify
        ok = mock.Mock(status_code=204)
        with mock.patch("requests.post", return_value=ok) as post:
            fanout._post("https://discord.invalid/w", "Intern @everyone")
            notify._discord("https://discord.invalid/w", "Intern @here")
        for call in post.call_args_list:
            self.assertEqual(call.kwargs["json"]["allowed_mentions"], {"parse": []})
        self.assertEqual(post.call_args_list[0].kwargs["json"]["content"], "Intern @everyone")

    # SEC-03
    def hidden_career_posting(self):
        _, pid = db.upsert_posting(self.conn, company="Stripe", title="SWE Intern 2027",
                                   url="https://stripe.com/j/1", source="career_page")
        self.conn.execute("UPDATE postings SET removed_at = 'x' WHERE id = ?", (pid,))
        self.conn.commit()
        return pid

    def removed_at(self, pid):
        return self.conn.execute("SELECT removed_at FROM postings WHERE id = ?",
                                 (pid,)).fetchone()["removed_at"]

    def test_old_alert_email_does_not_revive_a_closed_career_posting(self):
        pid = self.hidden_career_posting()
        db.upsert_posting(self.conn, company="Stripe", title="SWE Intern 2027",
                          url="https://www.linkedin.com/jobs/view/1", source="linkedin")
        self.assertEqual(self.removed_at(pid), "x")

    def test_career_page_still_revives_its_own_posting(self):
        pid = self.hidden_career_posting()
        db.upsert_posting(self.conn, company="Stripe", title="SWE Intern 2027",
                          url="https://stripe.com/j/1", source="career_page")
        self.assertEqual(self.removed_at(pid), "")

    def test_email_only_posting_can_still_be_revived_by_email(self):
        _, pid = db.upsert_posting(self.conn, company="Acme", title="Intern",
                                   url="https://www.linkedin.com/jobs/view/5", source="linkedin")
        self.conn.execute("UPDATE postings SET removed_at = 'x' WHERE id = ?", (pid,))
        db.upsert_posting(self.conn, company="Acme", title="Intern",
                          url="https://www.linkedin.com/jobs/view/5", source="linkedin")
        self.assertEqual(self.removed_at(pid), "")

    # SEC-06
    def test_companyless_postings_are_keyed_by_job_not_title(self):
        k = lambda url: db.dedupe_key(db.UNKNOWN_COMPANY, "Software Intern 2027", url)
        indeed = "https://www.indeed.com/rc/clk?jk={}&from=ja"
        self.assertNotEqual(k(indeed.format("aaa111")), k(indeed.format("bbb222")))
        self.assertEqual(k(indeed.format("aaa111")), k(indeed.format("AAA111") + "&x=1"))
        self.assertNotEqual(k("https://www.linkedin.com/comm/jobs/view/1?trk=a"),
                            k("https://www.linkedin.com/comm/jobs/view/2?trk=a"))
        self.assertEqual(k("https://www.linkedin.com/comm/jobs/view/1?trk=a"),
                         k("https://www.linkedin.com/jobs/view/1?trk=b"))
        # a job id only counts in its own site's host/path, not buried in a query
        self.assertEqual(k(indeed.format("aaa111") + "&x=linkedin.com/jobs/view/7"),
                         k(indeed.format("aaa111")))
        self.assertNotEqual(k("https://evil.example/?u=linkedin.com/jobs/view/7"),
                            k("https://www.linkedin.com/jobs/view/7"))

    def test_named_company_keys_are_unchanged(self):
        self.assertEqual(db.dedupe_key("Acme", "SWE Intern!", "https://x.com/1"), "acme|swe intern")
        self.assertEqual(db.dedupe_key("(manual)", "SWE Intern", "u"), "manual|swe intern")

    # SEC-07
    def test_sender_is_judged_by_address_domain(self):
        from tracker.gmail_source import _provider_for
        self.assertEqual(_provider_for("LinkedIn <jobalerts-noreply@linkedin.com>"), "linkedin")
        self.assertEqual(_provider_for("alert@indeed.com"), "indeed")
        self.assertEqual(_provider_for("x@e.linkedin.com"), "linkedin")
        self.assertIsNone(_provider_for('"LinkedIn Job Alerts" <alerts@evil.example>'))
        self.assertIsNone(_provider_for("jobs@linkedin.com.evil.example"))
        self.assertIsNone(_provider_for("indeed@evil.example"))


class TestRateLimitRetry(unittest.TestCase):
    def resp(self, status, retry_after=None, positions=None):
        r = mock.Mock(status_code=status, headers={})
        if retry_after is not None:
            r.headers["Retry-After"] = retry_after
        r.json.return_value = {"data": {"positions": positions or []}}
        r.raise_for_status.side_effect = (
            requests.HTTPError(f"{status}") if status >= 400 else None)
        return r

    def run_get(self, *responses):
        session = mock.Mock()
        session.get.side_effect = list(responses)
        with mock.patch("tracker.http_util.time.sleep") as sleep, \
                mock.patch("sys.stdout", io.StringIO()):
            r = get_with_backoff(session, "https://x.invalid", "X")
        return r, [c.args[0] for c in sleep.call_args_list], session.get.call_count

    def test_recovers_after_a_429(self):
        r, slept, calls = self.run_get(self.resp(429), self.resp(200))
        self.assertEqual((r.status_code, slept, calls), (200, [20], 2))

    def test_honors_retry_after_but_caps_it(self):
        _, slept, _ = self.run_get(self.resp(429, "5"), self.resp(200))
        self.assertEqual(slept, [5])
        _, slept, _ = self.run_get(self.resp(429, "3600"), self.resp(200))
        self.assertEqual(slept, [90])
        for junk in ("Wed, 21 Oct 2026 07:28:00 GMT", "²", "9" * 5000, "-5", "1.5"):
            _, slept, _ = self.run_get(self.resp(429, junk), self.resp(200))
            self.assertEqual(slept, [20], junk[:20])

    def test_gives_up_after_two_retries(self):
        r, slept, calls = self.run_get(self.resp(429), self.resp(429), self.resp(429))
        self.assertEqual((r.status_code, slept, calls), (429, [20, 60], 3))

    def test_other_errors_are_not_retried(self):
        r, slept, calls = self.run_get(self.resp(500))
        self.assertEqual((r.status_code, slept, calls), (500, [], 1))

    def test_eightfold_retries_then_pages_through(self):
        from tracker.adapters import fetch_eightfold
        page = [{"name": "SWE Intern", "id": i, "locations": ["Redmond, WA"]} for i in range(10)]
        session = mock.Mock()
        session.get.side_effect = [self.resp(429), self.resp(200, positions=page),
                                   self.resp(200, positions=page[:3]), self.resp(200)]
        company = {"name": "Microsoft", "host": "careers.example", "domain": "example.com"}
        with mock.patch("tracker.http_util.time.sleep"), mock.patch("tracker.adapters.time.sleep"), \
                mock.patch("sys.stdout", io.StringIO()):
            jobs = fetch_eightfold(company, session)
        self.assertEqual(len(jobs), 13)

    def test_eightfold_still_fails_when_throttled_throughout(self):
        from tracker.adapters import fetch_eightfold
        session = mock.Mock()
        session.get.side_effect = [self.resp(429)] * 3
        company = {"name": "Microsoft", "host": "careers.example", "domain": "example.com"}
        with mock.patch("tracker.http_util.time.sleep"), mock.patch("sys.stdout", io.StringIO()):
            with self.assertRaises(requests.HTTPError):
                fetch_eightfold(company, session)


class TestDashboard(unittest.TestCase):
    # SEC-01 and SEC-05
    def setUp(self):
        from tracker.dashboard import create_app
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        conn = db.connect(self.path)
        db.upsert_posting(conn, company="Acme", title="Intern", url="javascript:alert(1)",
                          source="manual")
        conn.close()
        cfg = dict(CFG, database=self.path, dashboard={"host": "127.0.0.1", "port": 5717})
        self.client = create_app(cfg).test_client()

    def tearDown(self):
        os.unlink(self.path)

    def post(self, path, data, **headers):
        return self.client.post(path, data=data, headers=headers)

    def test_own_forms_still_work(self):
        r = self.post("/status/1", {"status": "Applied"}, Origin="http://localhost")
        self.assertEqual(r.status_code, 302)
        r = self.post("/add", {"url": "https://acme.com/j"}, Referer="http://localhost/?status=All")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_cross_site_post_is_refused(self):
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Referer": "https://evil.example/page"}, {},
                        {"Origin": "http://localhost:9999"}):
            r = self.post("/add", {"url": "https://phish.example"}, **headers)
            self.assertEqual(r.status_code, 403, headers)

    def test_dns_rebinding_is_refused(self):
        r = self.client.get("/", headers={"Host": "evil.example:5717"})
        self.assertEqual(r.status_code, 403)
        r = self.post("/add", {"url": "https://phish.example"},
                      Host="evil.example:5717", Origin="http://evil.example:5717")
        self.assertEqual(r.status_code, 403)

    def test_dangerous_links_are_not_clickable(self):
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn("javascript:", page)
        self.assertIn('href="#"', page)


class TestStaticFeed(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.conn = db.connect(self.path)
        self.out = self.path + ".html"

    def tearDown(self):
        self.conn.close()
        for p in (self.path, self.out):
            if os.path.exists(p):
                os.unlink(p)

    def render(self, cfg=None):
        render(self.conn, self.out, cfg)
        with open(self.out, encoding="utf-8") as f:
            html = f.read()
        data = re.search(r'<script type="application/json" id="feed-data">(.*?)</script>',
                         html, re.S).group(1)
        return html, json.loads(data)

    def add(self, title, company="Acme"):
        return db.upsert_posting(self.conn, company=company, title=title,
                                 url="https://acme.com/j", source="career_page")[1]

    def test_script_breakout_is_neutralised(self):
        payload = "Intern</script><script>alert(1)</script><!--"
        self.add(payload)
        html, data = self.render()
        self.assertEqual(html.count("</script>"), 2)  # only the template's own
        self.assertEqual(data[0]["title"], payload)  # and the data survives intact

    def test_placeholder_in_posting_is_not_substituted(self):
        self.add("Intern __HEALTH__ __DATA__")
        _, data = self.render()
        self.assertEqual(data[0]["title"], "Intern __HEALTH__ __DATA__")

    def test_hidden_postings_are_left_off(self):
        pid = self.add("Closed Intern")
        self.add("Open Intern")
        self.conn.execute("UPDATE postings SET removed_at = 'x' WHERE id = ?", (pid,))
        self.conn.commit()
        _, data = self.render()
        self.assertEqual([d["title"] for d in data], ["Open Intern"])

    def test_logo_only_from_a_valid_configured_domain(self):
        self.add("Intern A", company="Acme")
        self.add("Intern B", company="Evil")
        self.add("Intern C", company="Unlisted")
        cfg = {"companies": [
            {"name": "Acme", "website": "acme.com"},
            {"name": "Evil", "website": 'evil.com" onerror="alert(1)'},
        ]}
        _, data = self.render(cfg)
        sites = {d["company"]: d["site"] for d in data}
        self.assertEqual(sites, {"Acme": "acme.com", "Evil": "", "Unlisted": ""})


class TestEmailParsing(unittest.TestCase):
    def test_linkedin(self):
        html = """
        <a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trk=x">
          <strong>Software Engineer Intern, Summer 2027</strong></a>
        <span>Datadog &#183; New York, NY</span>
        <a href="https://www.linkedin.com/comm/jobs/view/4012345678/?dup=1">
          Software Engineer Intern, Summer 2027</a>
        <a href="https://www.linkedin.com/e/v2?unsubscribe">Unsubscribe</a>
        """
        jobs = extract_jobs_from_html(html, "linkedin")
        self.assertEqual(len(jobs), 1)          # same job id deduped
        self.assertEqual(jobs[0]["company"], "Datadog")

    def test_indeed(self):
        html = ('<a href="https://www.indeed.com/viewjob?jk=999888777">'
                'Embedded Systems Intern</a><span>John Deere &#8226; Moline, IL</span>')
        jobs = extract_jobs_from_html(html, "indeed")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["company"], "John Deere")


if __name__ == "__main__":
    unittest.main(verbosity=2)
