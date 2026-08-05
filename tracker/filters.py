"""Decide whether a raw posting is a relevant undergrad internship."""
import re

# US signals: country names, state abbreviations after a comma, or "US Remote"-style
US_RE = re.compile(
    r"United States|USA|\bU\.?S\.?\b|,\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|"
    r"LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|"
    r"VA|WA|WV|WI|WY|DC)\b", re.I)
# Obvious non-US markers, used to decide whether a bare "Remote" is US remote
FOREIGN_RE = re.compile(
    r"United Kingdom|\bUK\b|London|Canada|Toronto|Vancouver|Montreal|India|Bangalore|Bengaluru|"
    r"Hyderabad|Gurgaon|Mumbai|China|Shanghai|Beijing|Suzhou|Shenzhen|Japan|Tokyo|Germany|"
    r"Munich|Berlin|Hamburg|Ireland|Dublin|Cork|Singapore|Israel|Tel Aviv|Haifa|Switzerland|"
    r"Zurich|France|Paris|Netherlands|Amsterdam|Spain|Madrid|Poland|Warsaw|Australia|Sydney|"
    r"Melbourne|Brazil|Mexico|Taiwan|Taipei|Korea|Seoul|Sweden|Denmark|Italy|Austria|Czech|"
    r"Romania|Hungary|Belgium|Norway|Finland|Portugal|Egypt|Nigeria|Kenya|Vietnam|Thailand|"
    r"Philippines|Indonesia|Malaysia|Hong Kong|New Zealand|Argentina|Colombia|Chile|Costa Rica|"
    r"United Arab Emirates|Dubai|Saudi|Qatar|Turkey|South Africa", re.I)
REMOTE_RE = re.compile(r"\bremote\b|\bvirtual\b", re.I)
UNKNOWN_RE = re.compile(r"^\s*$|^\d+\s+locations?$", re.I)  # e.g. Adobe's "3 Locations"


class PostingFilter:
    def __init__(self, cfg):
        f = cfg.get("filters", {})
        # Gate: does this look like an internship at all?
        # (base_include_title preferred; falls back to legacy include_title)
        self.base_include = re.compile(
            f.get("base_include_title") or f.get("include_title", r"\bintern"), re.I)
        self.exclude = re.compile(f.get("exclude_title", r"\bphd\b"), re.I)
        self.term = re.compile(f.get("term", r"\b(2026|2027)\b"), re.I)
        self.keep_if_no_term_info = bool(f.get("keep_if_no_term_info", True))
        # Role categories: [(name, include_re, exclude_re-or-None), ...]
        self.categories = []
        for c in f.get("categories", []) or []:
            self.categories.append((
                c["name"],
                re.compile(c["include_title"], re.I),
                re.compile(c["exclude_title"], re.I) if c.get("exclude_title") else None,
            ))
        # Postings that pass the gate but match no category get tagged
        # "general" (true) or dropped (false).
        self.keep_uncategorized = bool(f.get("keep_uncategorized", True))
        loc = f.get("location", {}) or {}
        self.us_only = bool(loc.get("us_only", False))
        self.preferred_states = {s.upper() for s in loc.get("preferred_states", [])}
        # Back-compat: derive preference from the old regex key if the new
        # list isn't set (old configs had `prefer:` matching California).
        if not self.preferred_states and loc.get("prefer"):
            self.preferred_states = {"CA"}

    def location_ok(self, location, source_us_filtered=False):
        """US-only check (remote-friendly). Unknown locations are kept."""
        if not self.us_only or source_us_filtered:
            return True
        loc = location or ""
        if UNKNOWN_RE.match(loc):
            return True  # no info to judge by; the title/term filters still apply
        if US_RE.search(loc):
            return True
        if REMOTE_RE.search(loc) and not FOREIGN_RE.search(loc):
            return True
        return False

    def is_preferred_state(self, state_field):
        from .locations import split_states
        return bool(self.preferred_states.intersection(split_states(state_field)))

    def accept(self, title, description="", category_hint=None):
        """Classify a posting. Returns (categories, reason).

        `categories` is a list of matched category names — empty means the
        posting is rejected. A posting can match several categories.
        `category_hint` is the category whose search term fetched this
        posting (used to tag results Amazon's category facet pre-filtered).
        """
        title = title or ""
        if not self.base_include.search(title):
            return [], "title lacks intern/co-op keyword"
        if self.exclude.search(title):
            return [], "title matches exclusion (PhD/senior)"
        blob = f"{title}\n{description or ''}"
        if not self.term.search(blob):
            if not self.keep_if_no_term_info:
                return [], "no 2026/2027/season mention"
        cats = [name for name, inc, exc in self.categories
                if inc.search(title) and not (exc and exc.search(title))]
        if not cats:
            if category_hint:
                cats = [category_hint]  # source-side facet already vouched for it
            elif self.keep_uncategorized or not self.categories:
                cats = ["general"]
            else:
                return [], "no category match"
        return sorted(set(cats)), "ok"
