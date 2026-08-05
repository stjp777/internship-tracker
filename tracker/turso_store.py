"""Turso (libSQL) backend over the plain HTTPS pipeline API.

Implements just enough of the sqlite3 connection interface that the rest
of the codebase (tracker.db & friends) works unchanged against a hosted
Turso database: .execute(sql, params) / .executemany / .executescript /
.commit / .close, with rows that support row["col"] access.

Using the HTTP API instead of the native libsql driver keeps this
dependency-free (just `requests`) and identical across Windows machines
and GitHub Actions runners.
"""
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

    def _pipeline(self, stmts):
        body = {"requests": [{"type": "execute", "stmt": s} for s in stmts]
                + [{"type": "close"}]}
        r = self.session.post(f"{self.url}/v2/pipeline", json=body, timeout=self.timeout)
        r.raise_for_status()
        results = []
        for item in r.json().get("results", []):
            if item.get("type") == "error":
                raise RuntimeError(f"turso: {item.get('error', {}).get('message')}")
            if "response" in item and item["response"].get("type") == "execute":
                results.append(item["response"]["result"])
        return results

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
        stmts = [{"sql": s.strip()} for s in script.split(";") if s.strip()]
        if stmts:
            self._pipeline(stmts)
        return _Result([])

    def commit(self):
        pass  # every pipeline auto-commits

    def close(self):
        self.session.close()
