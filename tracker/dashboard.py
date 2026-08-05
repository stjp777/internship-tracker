"""Local web dashboard: browse postings, set status, quick-add a URL."""
import json

from flask import Flask, redirect, render_template_string, request

from . import db
from .filters import PostingFilter
from .locations import split_states

PAGE = """
<!doctype html><html><head><meta charset="utf-8">
<title>Internship Tracker</title>
<style>
 :root { --bg:#fff; --fg:#1a1a1a; --muted:#667; --line:#e3e3e8; --accent:#2563eb;
         --new:#dcfce7; --applied:#dbeafe; --dismissed:#f3f4f6; --chip:#eef2ff; }
 @media (prefers-color-scheme: dark) {
   :root { --bg:#111418; --fg:#e8e8ea; --muted:#9aa; --line:#2a2f36; --accent:#60a5fa;
           --new:#14532d; --applied:#1e3a8a; --dismissed:#1f2937; --chip:#1e2740; } }
 body { font: 14px/1.5 system-ui, sans-serif; margin: 0 auto; max-width: 1150px;
        padding: 1.5rem; background: var(--bg); color: var(--fg); }
 h1 { font-size: 1.3rem; } a { color: var(--accent); }
 table { border-collapse: collapse; width: 100%; }
 th, td { text-align: left; padding: .45rem .6rem; border-bottom: 1px solid var(--line);
          vertical-align: top; }
 th { font-size: .75rem; text-transform: uppercase; color: var(--muted); }
 tr.status-New    { background: var(--new); }
 tr.status-Applied{ background: var(--applied); }
 tr.status-Dismissed { background: var(--dismissed); opacity:.65; }
 .facets { margin: .3rem 0; }
 .facets .lbl { display:inline-block; width: 4.5rem; font-size:.72rem;
   text-transform: uppercase; color: var(--muted); }
 .facets a { margin-right: .55rem; text-decoration: none; white-space: nowrap; }
 .facets a.active { font-weight: 700; text-decoration: underline; }
 .facets .n { color: var(--muted); font-size: .75rem; }
 .btns form { display: inline; }
 .btns button { font-size: .7rem; margin-right: .15rem; cursor: pointer;
   border: 1px solid var(--line); background: var(--bg); color: var(--fg);
   border-radius: 4px; padding: .15rem .4rem; }
 .quickadd { margin: .8rem 0; }
 .quickadd input[type=text] { padding: .35rem; border: 1px solid var(--line);
   border-radius: 4px; background: var(--bg); color: var(--fg); }
 .muted { color: var(--muted); font-size: .8rem; }
 .health { margin-top: 2rem; font-size: .75rem; color: var(--muted); }
 .health .err { color: #dc2626; }
 .src { display:inline-block; font-size:.68rem; border:1px solid var(--line);
        border-radius:8px; padding:0 .4rem; margin-right:.2rem; }
 .st { display:inline-block; font-size:.68rem; background: var(--chip);
       border-radius:8px; padding:0 .4rem; margin-right:.2rem; }
</style></head><body>
<h1>Internship Tracker <span class="muted">({{ rows|length }} shown)</span></h1>

<div class="facets"><span class="lbl">Status</span>
  {% for t in ["All","New","Viewed","Applied","Dismissed"] %}
  <a href="{{ mkurl(status=t) }}" class="{{ 'active' if t == sel_status else '' }}">{{ t }}</a>
  {% endfor %}
</div>
<div class="facets"><span class="lbl">State</span>
  <a href="{{ mkurl(state='All') }}" class="{{ 'active' if sel_state == 'All' else '' }}">All</a>
  {% for s, n in state_facet %}
  <a href="{{ mkurl(state=s) }}" class="{{ 'active' if s == sel_state else '' }}">{{ s }} <span class="n">{{ n }}</span></a>
  {% endfor %}
</div>
<div class="facets"><span class="lbl">Category</span>
  <a href="{{ mkurl(category='All') }}" class="{{ 'active' if sel_cat == 'All' else '' }}">All</a>
  {% for s, n in cat_facet %}
  <a href="{{ mkurl(category=s) }}" class="{{ 'active' if s == sel_cat else '' }}">{{ s }} <span class="n">{{ n }}</span></a>
  {% endfor %}
</div>

<form class="quickadd" method="post" action="/add">
  <input type="text" name="url" placeholder="Quick add: paste a job URL (e.g. from Handshake)" size="46" required>
  <input type="text" name="company" placeholder="Company" size="14">
  <input type="text" name="title" placeholder="Title" size="24">
  <button>Add</button>
</form>

<table>
<tr><th>First seen</th><th>Company</th><th>Title</th><th>Location</th>
    <th>Sources</th><th>Status</th><th></th></tr>
{% for r in rows %}
<tr class="status-{{ r['status'] }}">
  <td class="muted" title="{{ r['first_seen'] }}">{{ r['first_seen'][:16].replace('T',' ') }}</td>
  <td>{{ r['company'] }}</td>
  <td>{% if r['is_pref'] %}<span title="preferred state">⭐</span> {% endif %}<a href="{{ r['url'] }}" target="_blank" rel="noopener">{{ r['title'] }}</a>
      {% if r['deadline'] %}<div class="muted">deadline: {{ r['deadline'] }}</div>{% endif %}</td>
  <td class="muted">{% for s in r['states'] %}<span class="st">{{ s }}</span>{% endfor %}
      <div>{{ r['location'][:55] }}</div></td>
  <td>{% for s in r['sources_list'] %}<span class="src">{{ s }}</span>{% endfor %}
      {% for s in r['cats'] %}<span class="st">{{ s }}</span>{% endfor %}</td>
  <td>{{ r['status'] }}</td>
  <td class="btns">
    {% for s in ["Viewed","Applied","Dismissed","New"] if s != r['status'] %}
    <form method="post" action="/status/{{ r['id'] }}">
      <input type="hidden" name="status" value="{{ s }}">
      <input type="hidden" name="back" value="{{ backparams }}">
      <button>{{ s }}</button>
    </form>
    {% endfor %}
  </td>
</tr>
{% endfor %}
</table>

<div class="health">
<b>Source health</b><br>
{% for h in health %}
  {{ h['source'] }}: last ok {{ (h['last_ok'] or 'never')[:16].replace('T',' ') }}
  {% if h['error_msg'] %}<span class="err"> — last error {{ (h['last_error'] or '')[:16].replace('T',' ') }}: {{ h['error_msg'][:120] }}</span>{% endif %}
  <br>
{% endfor %}
</div>
</body></html>
"""


def create_app(cfg):
    app = Flask(__name__)
    db_path = cfg.get("database", "internships.db")

    def conn():
        return db.connect(db_path)

    @app.route("/")
    def index():
        sel_status = request.args.get("status", "All")
        sel_state = request.args.get("state", "All")
        sel_cat = request.args.get("category", "All")
        pf = PostingFilter(cfg)
        c = conn()
        q = "SELECT * FROM postings"
        params = ()
        if sel_status != "All":
            q += " WHERE status = ?"
            params = (sel_status,)
        q += " ORDER BY first_seen DESC, id DESC LIMIT 500"
        rows = [dict(r) for r in c.execute(q, params).fetchall()]
        for r in rows:
            r["sources_list"] = json.loads(r["sources"])
            r["states"] = split_states(r["state"])
            r["cats"] = json.loads(r["categories"] or "[]") or ["general"]
            r["is_pref"] = pf.is_preferred_state(r["state"])
        # facet counts within the current status selection
        s_counts, c_counts = {}, {}
        for r in rows:
            for s in r["states"]:
                s_counts[s] = s_counts.get(s, 0) + 1
            for s in r["cats"]:
                c_counts[s] = c_counts.get(s, 0) + 1
        state_facet = sorted(
            s_counts.items(),
            key=lambda kv: (kv[0] in ("REMOTE", "UNKNOWN"), kv[0]))
        cat_facet = sorted(c_counts.items(), key=lambda kv: (kv[0] == "general", kv[0]))
        if sel_state != "All":
            rows = [r for r in rows if sel_state in r["states"]]
        if sel_cat != "All":
            rows = [r for r in rows if sel_cat in r["cats"]]
        health = c.execute("SELECT * FROM source_health ORDER BY source").fetchall()
        c.close()

        def mkurl(status=None, state=None, category=None):
            return "/?status=%s&state=%s&category=%s" % (
                status or sel_status, state or sel_state, category or sel_cat)

        return render_template_string(
            PAGE, rows=rows, sel_status=sel_status, sel_state=sel_state,
            sel_cat=sel_cat, state_facet=state_facet, cat_facet=cat_facet,
            health=health, mkurl=mkurl,
            backparams=f"{sel_status}|{sel_state}|{sel_cat}")

    @app.route("/status/<int:pid>", methods=["POST"])
    def set_status(pid):
        c = conn()
        db.set_status(c, pid, request.form["status"])
        c.close()
        parts = (request.form.get("back", "All|All|All").split("|") + ["All"] * 3)[:3]
        return redirect(f"/?status={parts[0] or 'All'}&state={parts[1] or 'All'}"
                        f"&category={parts[2] or 'All'}")

    @app.route("/add", methods=["POST"])
    def add():
        url = request.form["url"].strip()
        company = request.form.get("company", "").strip() or "(manual)"
        title = request.form.get("title", "").strip() or url[:120]
        cats, _ = PostingFilter(cfg).accept(title)
        c = conn()
        verdict, _id = db.upsert_posting(
            c, company=company, title=title, url=url, source="manual",
            categories=cats or ["general"])
        # manual adds shouldn't ping you about themselves
        db.mark_notified(c, [_id])
        c.close()
        return redirect("/")

    return app
