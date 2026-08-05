"""Shared HTTP session with polite defaults and a robots.txt check."""
import re
from urllib.parse import urlsplit

import requests

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
                  " (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

_robots_cache = {}


def _parse_robots(text):
    """RFC 9309 grouping: consecutive User-agent lines share the rule
    block that follows them. Returns [(agents, [(is_allow, path)])]."""
    groups, agents, rules = [], [], []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key, val = key.strip().lower(), val.strip()
        if key == "user-agent":
            if rules:
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(val.lower())
        elif key in ("allow", "disallow") and agents:
            rules.append((key == "allow", val))
    if agents:
        groups.append((agents, rules))
    return groups


def _rule_matches(pattern, path):
    """robots path match with * (any chars) and trailing $ (end anchor)."""
    regex = "".join(
        ".*" if ch == "*" else re.escape(ch) for ch in pattern.rstrip("$"))
    if pattern.endswith("$"):
        regex += "$"
    return re.match(regex, path) is not None


def _robots_allowed(groups, user_agent, path):
    """Group selection + longest-match rule, Allow wins length ties."""
    ua = user_agent.lower()
    chosen = []
    if ua != "*":
        for agents, rules in groups:
            if any(a != "*" and a in ua for a in agents):
                chosen.extend(rules)
    if not chosen:
        for agents, rules in groups:
            if "*" in agents:
                chosen.extend(rules)
    if not chosen:
        return True
    best_len, best_allow = -1, True
    for is_allow, rule_path in chosen:
        if not rule_path:
            continue  # empty Disallow means allow-everything; no match
        if _rule_matches(rule_path, path):
            if len(rule_path) > best_len or (len(rule_path) == best_len and is_allow):
                best_len, best_allow = len(rule_path), is_allow
    return best_allow


def robots_allows(url, user_agent="*"):
    """robots.txt check for HTML page fetches (RFC 9309 semantics —
    urllib.robotparser mis-handles shared user-agent groups and Allow
    precedence). Unreachable robots.txt counts as allowed."""
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    if origin not in _robots_cache:
        try:
            resp = requests.get(origin + "/robots.txt",
                                headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}, timeout=15)
            groups = _parse_robots(resp.text) if resp.status_code == 200 else None
            debug = f"status={resp.status_code} len={len(resp.text)}"
        except requests.RequestException as e:
            groups, debug = None, f"fetch failed: {e}"
        _robots_cache[origin] = (groups, debug)
    groups, debug = _robots_cache[origin]
    if groups is None:
        return True
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    allowed = _robots_allowed(groups, user_agent, path or "/")
    if not allowed:
        print(f"[robots] {origin} disallows {path} ({debug})")
    return allowed


def make_session():
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    return s
