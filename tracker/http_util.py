"""Shared HTTP session with polite defaults and a robots.txt check."""
import time
import urllib.robotparser
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


def robots_allows(url, user_agent="*"):
    """Best-effort robots.txt check for HTML page fetches.
    Unreachable/unparseable robots.txt counts as allowed."""
    origin = "{0.scheme}://{0.netloc}".format(urlsplit(url))
    rp = _robots_cache.get(origin)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            resp = requests.get(origin + "/robots.txt",
                                headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}, timeout=15)
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                rp._debug = f"status=200 len={len(resp.text)} head={resp.text[:120]!r}"
            else:
                rp.allow_all = True
                rp._debug = f"status={resp.status_code} -> allow_all"
        except requests.RequestException as e:
            rp.allow_all = True
            rp._debug = f"fetch failed ({e}) -> allow_all"
        _robots_cache[origin] = rp
    try:
        allowed = rp.can_fetch(user_agent, url)
    except Exception:
        return True
    if not allowed:
        print(f"[robots] {origin} disallows {url} ({getattr(rp, '_debug', '?')})")
    return allowed


def make_session():
    s = requests.Session()
    s.headers.update(BROWSER_HEADERS)
    return s


def polite_sleep(seconds):
    if seconds > 0:
        time.sleep(seconds)
