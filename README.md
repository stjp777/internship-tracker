# Internship Tracker

Aggregates Summer 2027 / Winter 2026 internship postings for a CS undergrad
from company career pages, LinkedIn/Indeed alert emails (via Gmail), and
manual quick-adds — into one local SQLite database with a web dashboard and
per-posting notifications.

**This tool never submits applications.** It only scrapes, parses,
deduplicates, stores, and notifies. You always apply manually.

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
of the local copy — use these when the cloud poller is the thing keeping
data fresh. Statuses you set in shared mode are visible to everyone using
that database; plain `serve` keeps them private to this machine.

Run the tests with `python -m unittest discover -s tests` (offline — no
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
| LinkedIn, Indeed | **never scraped** — parsed from their alert emails in Gmail |
| Handshake        | manual quick-add (dashboard form or `python tracker.py add <url>`) |

Filtering (see `filters:` in config.yaml): title must mention
intern/co-op/apprentice (or Google's "Student Researcher"); PhD/senior/
staff/principal/director titles are dropped; timing keywords
(2026/2027/summer/winter/...) are checked in the title+description.

**Region: United States only** (`filters.location.us_only`). US-remote
postings are kept; postings with no location info are kept rather than
risk missing something. Microsoft, Apple, and Google are additionally
filtered to the US server-side. California postings get a ⭐ and their own
dashboard tab (`filters.location.prefer` controls what counts as CA).
Set `us_only: false` to go back to worldwide.

## Gmail setup (one-time, ~5 minutes)

1. Create your own LinkedIn and Indeed **job alerts** on their sites
   (saved search → email frequency: daily or instant).
2. Go to https://console.cloud.google.com/ → create a project (any name).
3. "APIs & Services" → "Library" → enable **Gmail API**.
4. "APIs & Services" → "OAuth consent screen" → External → add yourself
   (stevebull07102007@gmail.com) as a test user.
5. "Credentials" → "Create credentials" → **OAuth client ID** →
   Application type: **Desktop app** → download the JSON.
6. Save it as `credentials.json` in this folder.
7. Run `python tracker.py gmail` — a browser window opens once to authorize
   (read-only scope). A `token.json` is cached afterwards.

Until `credentials.json` exists, the Gmail source is silently skipped.

## Notifications

- **Desktop toasts** are on by default — one per new posting; clicking a
  toast opens the job page.
- **Discord webhook** (recommended if you want pings on your phone): in any
  Discord server → channel settings → Integrations → Webhooks → New Webhook
  → Copy URL, then paste it into `notifications.discord_webhook` in
  config.yaml.
- If a single poll finds a large batch (> `max_individual_per_run`), you get
  one summary notification instead of a flood.

## Running it automatically

Option A — leave a terminal running: `python tracker.py daemon`

Option B — auto-start hidden at logon (Task Scheduler):

```
powershell -ExecutionPolicy Bypass -File setup_autostart.ps1
```

Remove later with `Unregister-ScheduledTask -TaskName InternshipTracker`.

Schedule knobs are in `schedule:` in config.yaml (hourly career pages,
20-minute Gmail checks, 7:00–23:00 active window, slower at night).

## Adding companies

Greenhouse / Lever / Ashby boards need zero code — add to config.yaml:

```yaml
  - name: Figma
    type: greenhouse       # or lever / ashby
    board: figma           # the company's board token (visible in their careers URL)
```

Workday companies: copy the NVIDIA entry and adjust `host`/`tenant`/`site`
(all visible in the careers site URL).

## Shared / hosted mode (friends + zero local hosting)

Career-page polling can run centrally on GitHub Actions (every 30 min),
writing to a shared Turso database, pinging each friend's Discord webhook
per their preferences, and publishing a read-only dashboard to GitHub
Pages. Nothing personal is centralized: Gmail parsing stays on each
person's own machine.

**Architecture**

- `cloud_poll.py` — what Actions runs: `poll_career_pages` → per-user
  Discord fan-out (`tracker/fanout.py`) → static dashboard render
  (`tracker/render_static.py`) → Pages deploy.
- `tracker/turso_store.py` — Turso over plain HTTPS; the same code paths
  work against local SQLite (default) or the shared db (when
  `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` are set). Local single-user
  mode is unchanged and stays supported.
- `users.yaml` (gitignored; see `users.example.yaml`) — friends, their
  webhooks, and their state/category preferences. Sync into the shared
  db with `python tracker.py sync-users`.
- Delivery is tracked **per user** via a `last_posting_id` watermark that
  only advances after their webhook accepts the message. A friend whose
  webhook is broken or offline gets retried on the next run instead of
  silently missing postings, and one person's outage never suppresses
  anyone else's. New users start caught-up (no backlog flood); editing
  their preferences later never rewinds or skips the watermark.
- The published feed carries a source-health footer, so a broken adapter
  (the likeliest being Meta rotating its GraphQL `doc_id`) is visible on
  the page instead of only in the Actions log.
- `gmail_push.py` — optional, LOCAL ONLY: parses your own Gmail alerts
  with your own OAuth token and pushes just the extracted postings to
  the shared db. OAuth credentials never leave your machine. The Turso
  write token is the trust boundary — only give it to friends you trust
  with the shared data, and never commit it anywhere.

**One-time setup**

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
`credentials.json`, `token.json`, and `*.db` are gitignored — keep it
that way. The public repo contains no tokens; Actions reads them from
repo secrets.

## Maintenance notes

- **Meta**: uses a captured GraphQL `doc_id` (Aug 2026). If Meta rotates it,
  the dashboard's "Source health" panel shows the error. Re-capture: open
  metacareers.com/jobs in DevTools → Network → filter `graphql` → search a
  keyword → copy `doc_id` from the request body into config.yaml.
- **Apple/Google**: HTML-embedded data; if their page structure changes the
  adapter fails loudly and shows in Source health. robots.txt is checked
  before each HTML fetch.
- Rate limits: one request batch per company per hour with a 5-second gap
  between companies — far below anything that gets flagged.
- The db is plain SQLite (`internships.db`); inspect it with any SQLite tool.
