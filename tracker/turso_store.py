"""Turso (libSQL) backend over the plain HTTPS pipeline API.

Implements just enough of the sqlite3 connection interface that the rest
of the codebase (tracker.db & friends) works unchanged against a hosted
Turso database: .execute(sql, params) / .executemany / .executescript /
.commit / .close, with rows that support row["col"] access.

Using the HTTP API instead of the native libsql driver keeps this
dependency-free (just `requests`) and identical across Windows machines
and GitHub Actions runners.
"""
import time

import requests


class _Result:
    def __init__(self, rows, lastrowid=None):
        self.rows = rows
        self.lastrowid = lastrowid

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


def _arg(v):
    if v is None:
        return {"type": "null", "value": None}
    if isinstance(v, bool):
        return {"type": "integer", "value": str(int(v))}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def _cell(c):
    t = c.get("type")
    v = c.get("value")
    if t == "integer":
        return int(v)
    if t == "float":
        return float(v)
    if t == "null":
        return None
    return v


class TursoConn:
    row_factory = None  # accepted and ignored for sqlite3 compatibility

    def __init__(self, url, auth_token, timeout=30):
        if url.startswith("libsql://"):
            url = "https://" + url[len("libsql://"):]
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
        })

    def _pipeline(self, stmts, retries=3):
        body = {"requests": [{"type": "execute", "stmt": s} for s in stmts]
                + [{"type": "close"}]}
        last_exc = None
        for attempt in range(retries):
            try:
                r = self.session.post(f"{self.url}/v2/pipeline", json=body,
                                      timeout=self.timeout)
                # 5xx and 429 are transient; 4xx means our request is wrong
                # and retrying it would just repeat the same failure.
                if r.status_code >= 500 or r.status_code == 429:
                    raise requests.HTTPError(f"HTTP {r.status_code}", response=r)
                r.raise_for_status()
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                last_exc = e
                if isinstance(e, requests.HTTPError) and e.response is not None \
                        and e.response.status_code < 500 and e.response.status_code != 429:
                    raise
                if attempt == retries - 1:
                    break
                time.sleep(2 ** attempt)  # 1s, 2s
                continue
            results = []
            for item in r.json().get("results", []):
                if item.get("type") == "error":
                    raise RuntimeError(f"turso: {item.get('error', {}).get('message')}")
                if "response" in item and item["response"].get("type") == "execute":
                    results.append(item["response"]["result"])
            return results
        raise last_exc

    def execute(self, sql, params=()):
        stmt = {"sql": sql}
        if params:
            stmt["args"] = [_arg(p) for p in params]
        res = self._pipeline([stmt])[0]
        cols = [c.get("name") for c in res.get("cols", [])]
        rows = [dict(zip(cols, (_cell(c) for c in row))) for row in res.get("rows", [])]
        last = res.get("last_insert_rowid")
        return _Result(rows, int(last) if last is not None else None)

    def executemany(self, sql, seq_of_params):
        stmts = [{"sql": sql, "args": [_arg(p) for p in params]} for params in seq_of_params]
        if stmts:
            self._pipeline(stmts)
        return _Result([])

    def executescript(self, script):
        # Strip `--` line comments before splitting on ';' — a comment that
        # itself contains a semicolon (e.g. "-- JSON list of codes; [] = all")
        # would otherwise truncate the statement mid-way and the server
        # rejects the fragment with "unexpected end of input".
        cleaned_lines = []
        for line in script.splitlines():
            idx = line.find("--")
            cleaned_lines.append(line[:idx] if idx != -1 else line)
        cleaned = "\n".join(cleaned_lines)
        stmts = [{"sql": s.strip()} for s in cleaned.split(";") if s.strip()]
        if stmts:
            self._pipeline(stmts)
        return _Result([])

    def commit(self):
        pass  # every pipeline auto-commits

    def close(self):
        self.session.close()
