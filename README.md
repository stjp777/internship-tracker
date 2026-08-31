# Internship Tracker

**Live feed: https://stjp777.github.io/internship-tracker/**

A tool I built to stop manually refreshing a dozen career pages every day
during internship season. It pulls fresh postings from company career
pages, LinkedIn/Indeed alert emails, and manual adds, then filters out
anything that isn't a real internship and dedupes what's left into one
place with a notification when something new shows up. It never applies
to anything for you. It just finds the postings and gets out of the way.

The live feed above runs on a schedule for free (GitHub Actions + GitHub
Pages + a Turso database), so there's nothing to install if you just want
to browse it. Everything below is for running your own copy or extending
it.

## Quick start

```
pip install -r requirements.txt

python tracker.py poll --seed    # first run: fill the db, no notification flood
python tracker.py serve          # dashboard at http://127.0.0.1:5717
python tracker.py daemon         # keep polling on schedule (Ctrl+C to stop)
```

Other commands: `poll` (one pass + notify), `poll --company NVIDIA`,
`gmail` (email check only), `add <url> --company X --title Y`, `list`,
`sync-users`.

`serve --shared` / `list --shared` read the shared Turso database instead
of the local copy. Use these when the cloud poller is the thing keeping
data fresh. Statuses you set in shared mode are visible to everyone using
that database; plain `serve` keeps them private to this machine.

Run the tests with `python -m unittest discover -s tests` (offline, no
network or credentials needed).

## How each source works

| Company   | Method |
|-----------|--------|
| Stripe, Duolingo | Greenhouse public JSON API |
| NVIDIA, Adobe    | Workday `wday/cxs` JSON API |
| Microsoft        | Eightfold `api/pcsx/search` JSON API |
| Amazon           | amazon.jobs `search.json` (software-development category) |
| Apple            | jobs.apple.com search page (embedded hydration JSON) |
| Google           | careers results page (server-rendered HTML, Intern & Apprentice level) |
| Meta             | metacareers GraphQL persisted query |
| LinkedIn, Indeed | **never scraped**: parsed from their alert emails in Gmail |
| Handshake        | manual quick-add (dashboard form or `python tracker.py add <url>`) |

Filtering (see `filters:` in config.yaml): title must mention
intern/co-op/apprentice (or Google's "Student Researcher"); PhD/senior/
staff/principal/director titles are dropped; timing keywords
(2026/2027/summer/winter/...) are checked in the title+description.

Region is US-only by default (`filters.location.us_only`). US-remote
postings are kept, and so is anything with no location listed at all
(better to see it than miss it). Microsoft, Apple, and Google get filtered
to the US server-side too. California postings get a star and their own
dashboard tab (`filters.location.prefer` controls what counts as CA).
Set `us_only: false` to go back to worldwide.

## Gmail setup (one-time)

1. Create your own LinkedIn and Indeed **job alerts** on their sites
   (saved search → email frequency: daily or instant).
2. Go to https://console.cloud.google.com/ → create a project (any name).
3. "APIs & Services" → "Library" → enable **Gmail API**.
4. "APIs & Services" → "OAuth consent screen" → External → add your own
   Google account as a test user.
5. "Credentials" → "Create credentials" → **OAuth client ID** →
   Application type: **Desktop app** → download the JSON.
6. Save it as `credentials.json` in this folder.
7. Run `python tracker.py gmail`. A browser window opens once to authorize
   (read-only scope), and a `token.json` is cached afterwards.

Until `credentials.json` exists, the Gmail source is silently skipped.

## Notifications

Desktop toasts are on by default, one per new posting. Click one and it
opens the job page. For pings on your phone, set up a Discord webhook: in
any Discord server, go to channel settings → Integrations → Webhooks →
New Webhook, copy the URL, and paste it into `notifications.discord_webhook`
in config.yaml. If a single poll turns up a big batch (more than
`max_individual_per_run`), you get one summary notification instead of a
flood.

## Running it automatically

Option A: leave a terminal running with `python tracker.py daemon`.

Option B: auto-start hidden at logon (Task Scheduler):

```
powershell -ExecutionPolicy Bypass -File setup_autostart.ps1
```

Remove later with `Unregister-ScheduledTask -TaskName InternshipTracker`.

Schedule knobs are in `schedule:` in config.yaml (hourly career pages,
20-minute Gmail checks, 7:00–23:00 active window, slower at night).

## Adding companies

Greenhouse / Lever / Ashby boards need zero code, just add to config.yaml:

```yaml
  - name: Figma
    type: greenhouse       # or lever / ashby
    board: figma           # the company's board token (visible in their careers URL)
```

Workday companies: copy the NVIDIA entry and adjust `host`/`tenant`/`site`
(all visible in the careers site URL).

## Shared / hosted mode (how the live feed above works)

The live feed at the top of this page isn't running on anyone's laptop.
Polling happens centrally on GitHub Actions every 30 minutes, results land
in a shared Turso database, each person's Discord webhook gets pinged
based on their own preferences, and a read-only dashboard gets published
to GitHub Pages. The one thing that stays local is Gmail parsing: nobody
else's inbox touches the shared database.

### How the pieces fit together

- `cloud_poll.py`: what Actions runs. `poll_career_pages` → per-user
  Discord fan-out (`tracker/fanout.py`) → static dashboard render
  (`tracker/render_static.py`) → Pages deploy.
- `tracker/turso_store.py`: Turso over plain HTTPS. The same code paths
  work against local SQLite (default) or the shared db (when
  `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` are set). Local single-user
  mode is unchanged and stays supported.
- `users.yaml` (gitignored; see `users.example.yaml`): friends, their
  webhooks, and their state/category preferences. Sync into the shared
  db with `python tracker.py sync-users`.
- Delivery is tracked per user via a `last_posting_id` watermark that only
  advances after their webhook accepts the message. A friend whose webhook
  is broken or offline gets retried on the next run instead of silently
  missing postings, and one person's outage never suppresses anyone
  else's. New users start caught up, no backlog flood, and editing their
  preferences later never rewinds or skips the watermark.
- The published feed carries a source-health footer, so a broken adapter
  (the likeliest being Meta rotating its GraphQL `doc_id`) is visible on
  the page instead of only in the Actions log.
- `gmail_push.py`: optional, LOCAL ONLY. Parses your own Gmail alerts
  with your own OAuth token and pushes just the extracted postings to
  the shared db. OAuth credentials never leave your machine. The Turso
  write token is the trust boundary, so only give it to friends you
  trust with the shared data, and never commit it anywhere.

### One-time setup

1. Turso: sign up at https://turso.tech (free tier), then
   `turso db create internships`, `turso db show internships --url`,
   `turso db tokens create internships`.
2. GitHub: create a public repo, push this folder, add repo secrets
   `TURSO_DATABASE_URL` and `TURSO_AUTH_TOKEN` (Settings → Secrets →
   Actions), and set Settings → Pages → Source to "GitHub Actions".
3. Seed without a notification flood: run locally once with the env vars
   set: `python cloud_poll.py --seed`.
4. `copy users.example.yaml users.yaml`, fill in real webhooks, then
   `python tracker.py sync-users` (with TURSO_* env vars set).
5. The workflow (`.github/workflows/poll.yml`) now runs on schedule; the
   Pages URL serves the shared feed. Personal statuses (Applied etc.)
   remain a local-dashboard feature.

Secrets hygiene: `config.local.yaml` (your webhook), `users.yaml`,
`credentials.json`, `token.json`, and `*.db` are gitignored, and it
should stay that way. The public repo contains no tokens; Actions reads
them from repo secrets.

## Maintenance notes

- **Meta**: uses a captured GraphQL `doc_id` (Aug 2026). If Meta rotates it,
  the dashboard's "Source health" panel shows the error. Re-capture: open
  metacareers.com/jobs in DevTools → Network → filter `graphql` → search a
  keyword → copy `doc_id` from the request body into config.yaml.
- **Apple/Google**: HTML-embedded data; if their page structure changes the
  adapter fails loudly and shows in Source health. robots.txt is checked
  before each HTML fetch.
- Rate limits: one request batch per company per hour with a 5-second gap
  between companies, far below anything that gets flagged.
- The db is plain SQLite (`internships.db`); inspect it with any SQLite tool.

---

Built with Claude Code as a development tool for scaffolding, debugging, and code review; all design decisions and final review are mine.
