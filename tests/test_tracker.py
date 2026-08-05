"""Offline unit tests: python -m unittest discover -s tests

No network, no credentials — safe to run anywhere, including CI.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import db  # noqa: E402
from tracker.fanout import _cats_match, _states_match  # noqa: E402
from tracker.filters import PostingFilter  # noqa: E402
from tracker.gmail_source import extract_jobs_from_html  # noqa: E402
from tracker.http_util import _parse_robots, _robots_allowed  # noqa: E402
from tracker.locations import state_tokens  # noqa: E402

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
