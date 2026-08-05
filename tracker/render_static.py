"""Render the shared postings list as a self-contained static HTML page.

Used by the GitHub Actions job to publish a read-only dashboard to
GitHub Pages after each poll. All filtering happens client-side in JS,
so the page needs no server. Statuses are personal and therefore absent
here — this page is a feed, not a tracker.
"""
import json
from datetime import datetime, timezone

from .locations import split_states

TEMPLATE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internship Feed</title>
<style>
 :root { --bg:#fff; --fg:#1a1a1a; --muted:#667; --line:#e3e3e8; --accent:#2563eb; --chip:#eef2ff; }
 @media (prefers-color-scheme: dark) {
   :root { --bg:#111418; --fg:#e8e8ea; --muted:#9aa; --line:#2a2f36; --accent:#60a5fa; --chip:#1e2740; } }
 body { font: 14px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 1100px;
        padding: 1.5rem 1rem; background: var(--bg); color: var(--fg); }
 h1 { font-size: 1.3rem; } a { color: var(--accent); }
 .muted { color: var(--muted); font-size: .8rem; }
 .facets { margin: .3rem 0; }
 .facets .lbl { display:inline-block; width: 5rem; font-size:.72rem;
   text-transform: uppercase; color: var(--muted); }
 .facets button { margin: 0 .3rem .3rem 0; cursor: pointer; font-size: .78rem;
   border: 1px solid var(--line); background: var(--bg); color: var(--fg);
   border-radius: 12px; padding: .1rem .6rem; }
 .facets button.on { background: var(--accent); color: #fff; border-color: var(--accent); }
 input[type=search] { padding: .35rem .6rem; border: 1px solid var(--line);
   border-radius: 6px; background: var(--bg); color: var(--fg); min-width: 260px; }
 table { border-collapse: collapse; width: 100%; margin-top: .8rem; }
 th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line);
          vertical-align: top; }
 th { font-size: .75rem; text-transform: uppercase; color: var(--muted); }
 .st { display:inline-block; font-size:.68rem; background: var(--chip);
       border-radius:8px; padding:0 .4rem; margin-right:.2rem; }
 @media (max-width: 700px) { .hide-sm { display: none; } }
 .health { margin-top: 2.5rem; font-size: .75rem; color: var(--muted);
           border-top: 1px solid var(--line); padding-top: .8rem; }
 .health .bad { color: #dc2626; font-weight: 600; }
</style></head><body>
<h1>Internship Feed <span id="count" class="muted"></span></h1>
<p class="muted">Updated __UPDATED__ UTC · refreshed every ~30 min ·
US postings only (remote included) · apply links go to the company's own site</p>
<p><input type="search" id="q" placeholder="Search title / company…"></p>
<div class="facets"><span class="lbl">State</span><span id="f-state"></span></div>
<div class="facets"><span class="lbl">Category</span><span id="f-cat"></span></div>
<div class="facets"><span class="lbl">Company</span><span id="f-co"></span></div>
<table><thead><tr><th>First seen</th><th>Company</th><th>Title</th>
<th class="hide-sm">Location</th></tr></thead><tbody id="rows"></tbody></table>
<div class="health">__HEALTH__</div>
<script>
const DATA = __DATA__;
const sel = { state: null, cat: null, co: null, q: "" };
function facet(elId, key, values) {
  const el = document.getElementById(elId);
  el.innerHTML = "";
  const mk = (label, val) => {
    const b = document.createElement("button");
    b.textContent = label;
    b.onclick = () => { sel[key] = (sel[key] === val ? null : val); render(); };
    if (sel[key] === val || (val === null && sel[key] === null)) b.classList.add("on");
    el.appendChild(b);
  };
  mk("All", null);
  values.forEach(([v, n]) => mk(v + " " + n, v));
}
function counts(rows, fn) {
  const m = {};
  rows.forEach(r => fn(r).forEach(v => { m[v] = (m[v] || 0) + 1; }));
  return Object.entries(m).sort((a, b) =>
    (["REMOTE","UNKNOWN"].includes(a[0]) - ["REMOTE","UNKNOWN"].includes(b[0]))
    || (a[0] === "general") - (b[0] === "general") || a[0].localeCompare(b[0]));
}
function render() {
  let rows = DATA;
  if (sel.q) { const q = sel.q.toLowerCase();
    rows = rows.filter(r => (r.title + " " + r.company).toLowerCase().includes(q)); }
  facet("f-state", "state", counts(rows, r => r.states));
  facet("f-cat", "cat", counts(rows, r => r.cats));
  facet("f-co", "co", counts(rows, r => [r.company]));
  if (sel.state) rows = rows.filter(r => r.states.includes(sel.state));
  if (sel.cat) rows = rows.filter(r => r.cats.includes(sel.cat));
  if (sel.co) rows = rows.filter(r => r.company === sel.co);
  document.getElementById("count").textContent = "(" + rows.length + " shown)";
  document.getElementById("rows").innerHTML = rows.map(r =>
    `<tr><td class="muted">${r.seen}</td><td>${r.company}</td>` +
    `<td><a href="${r.url}" target="_blank" rel="noopener">${r.title
        .replace(/&/g,"&amp;").replace(/</g,"&lt;")}</a><br>` +
    r.cats.map(c => `<span class="st">${c}</span>`).join("") + `</td>` +
    `<td class="hide-sm muted">${r.states.map(s => `<span class="st">${s}</span>`).join("")}` +
    ` ${r.location.replace(/&/g,"&amp;").replace(/</g,"&lt;")}</td></tr>`).join("");
}
document.getElementById("q").addEventListener("input", e => { sel.q = e.target.value; render(); });
render();
</script></body></html>
"""


def _health_html(conn):
    """Footer showing which sources are currently failing, so a broken
    adapter (e.g. Meta rotating its GraphQL doc_id) is visible on the page
    instead of only in the Actions log."""
    try:
        rows = conn.execute("SELECT * FROM source_health ORDER BY source").fetchall()
    except Exception:
        return ""
    broken = [r for r in rows if (r["error_msg"] or "").strip()]
    ok_names = [r["source"].replace("career:", "") for r in rows
                if not (r["error_msg"] or "").strip()]
    parts = []
    if broken:
        parts.append('<div class="bad">⚠ Sources failing — postings from these '
                     'may be missing:</div>')
        for r in broken:
            name = html_escape(r["source"].replace("career:", ""))
            msg = html_escape((r["error_msg"] or "")[:160])
            when = (r["last_error"] or "")[:16].replace("T", " ")
            parts.append(f'<div class="bad">{name}</div>'
                         f'<div>&nbsp;&nbsp;{when} UTC — {msg}</div>')
    if ok_names:
        parts.append("<div>Healthy sources: "
                     + html_escape(", ".join(sorted(ok_names))) + "</div>")
    return "\n".join(parts)


def html_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render(conn, out_path):
    rows = conn.execute(
        "SELECT * FROM postings WHERE status != 'Dismissed'"
        " ORDER BY first_seen DESC, id DESC LIMIT 1000").fetchall()
    data = []
    for r in rows:
        data.append({
            "seen": (r["first_seen"] or "")[:16].replace("T", " "),
            "company": r["company"],
            "title": r["title"],
            "url": r["url"],
            "location": (r["location"] or "")[:60],
            "states": split_states(r["state"]),
            "cats": json.loads(r["categories"] or "[]") or ["general"],
        })
    html = (TEMPLATE
            .replace("__UPDATED__", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"))
            .replace("__HEALTH__", _health_html(conn))
            .replace("__DATA__", json.dumps(data)))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    return len(data)
