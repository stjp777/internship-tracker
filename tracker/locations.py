"""Normalize free-form location strings into US state codes.

Handles the formats seen across adapters:
  "San Francisco, CA"                       -> ["CA"]
  "Mountain View, California"               -> ["CA"]
  "US, CA, Santa Clara"                     -> ["CA"]
  "United States, Washington, Redmond"      -> ["WA"]
  "Bellevue, WA; Menlo Park, CA"            -> ["CA", "WA"]   (Meta style)
  "Cupertino" / "San Francisco Bay Area"    -> ["CA"]         (Apple style, city map)
  "Remote" / "Virtual, US"                  -> ["REMOTE"]
  "" / "3 Locations" / "United States"      -> ["UNKNOWN"]

A posting can carry several tokens ("CA,WA" or "CA,REMOTE"). REMOTE and
UNKNOWN are pseudo-states so the dashboard can facet on them.
"""
import re

REMOTE = "REMOTE"
UNKNOWN = "UNKNOWN"

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
    "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV",
    "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
STATE_CODES = set(STATE_NAMES.values())

# Common tech-hub cities that appear with no state attached (mostly Apple).
CITY_STATES = {
    "cupertino": "CA", "sunnyvale": "CA", "santa clara": "CA", "san jose": "CA",
    "san francisco": "CA", "san francisco bay area": "CA", "bay area": "CA",
    "mountain view": "CA", "menlo park": "CA", "palo alto": "CA", "berkeley": "CA",
    "oakland": "CA", "los angeles": "CA", "san diego": "CA", "irvine": "CA",
    "sacramento": "CA", "culver city": "CA", "burlingame": "CA", "fremont": "CA",
    "seattle": "WA", "redmond": "WA", "bellevue": "WA", "kirkland": "WA",
    "new york city": "NY", "nyc": "NY", "brooklyn": "NY",
    "austin": "TX", "dallas": "TX", "houston": "TX",
    "boston": "MA", "cambridge (us)": "MA",
    "chicago": "IL", "denver": "CO", "boulder": "CO", "atlanta": "GA",
    "pittsburgh": "PA", "philadelphia": "PA", "miami": "FL", "phoenix": "AZ",
    "portland": "OR", "hillsboro": "OR", "salt lake city": "UT", "nashville": "TN",
    "raleigh": "NC", "durham": "NC", "charlotte": "NC", "minneapolis": "MN",
    "detroit": "MI", "ann arbor": "MI", "madison": "WI", "columbus": "OH",
    "arlington": "VA", "reston": "VA", "herndon": "VA", "washington dc": "DC",
}

# Uppercase-only code match so "in"/"or"/"me" in prose never count as states
_CODE_RE = re.compile(r"\b(" + "|".join(sorted(STATE_CODES)) + r")\b")
_REMOTE_RE = re.compile(r"\bremote\b|\bvirtual\b", re.I)
_NO_INFO_RE = re.compile(r"^\s*$|^\d+\s+locations?$|^(united states|usa?)$", re.I)


def state_tokens(location):
    """Return sorted list of state codes plus REMOTE/UNKNOWN pseudo-states."""
    loc = (location or "").strip()
    if _NO_INFO_RE.match(loc):
        return [UNKNOWN]
    states = set(_CODE_RE.findall(loc))
    low = loc.lower()
    for name, code in STATE_NAMES.items():
        if re.search(r"\b" + re.escape(name) + r"\b", low):
            states.add(code)
    for city, code in CITY_STATES.items():
        if re.search(r"\b" + re.escape(city) + r"\b", low):
            states.add(code)
    # "Washington, DC" means the district, not WA state
    if "DC" in states and "WA" in states and not re.search(r"\bWA\b", loc) \
            and "washington state" not in low:
        states.discard("WA")
    remote = bool(_REMOTE_RE.search(loc))
    if remote:
        states.add(REMOTE)
    if not states:
        return [UNKNOWN]
    return sorted(states)


def states_str(location):
    """Comma-joined token string for the db `state` column."""
    return ",".join(state_tokens(location))


def split_states(state_field):
    """Inverse of states_str for rows read back from the db."""
    return [s for s in (state_field or "").split(",") if s] or [UNKNOWN]
