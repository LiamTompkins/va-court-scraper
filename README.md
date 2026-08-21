# Virginia Court Scraper

Collects public case information from [Virginia's district and circuit court
case-information websites](https://eapps.courts.state.va.us/) into a PostgreSQL
database, and provides a live web dashboard to run and monitor the collection.
Data collected this way powers projects such as
[VirginiaCourtData.org](http://virginiacourtdata.org).

> **Looking for the original instructions?** The previous README is kept intact
> as [`README.legacy.md`](README.legacy.md). This file supersedes it.

---

## What's in this repo

| Piece | What it does |
| --- | --- |
| **Scraper** (`court_bulk_*`, `courtreader/`) | Claims collection *tasks* and scrapes case data into PostgreSQL |
| **Dashboard** (`worker_dashboard.py`) | Browser UI to schedule tasks, **start/stop workers with buttons**, watch progress, browse/export collected data, and run conflict checks |
| **Supervisor** (`worker_supervisor.py`) | Spawns and stops the actual collector processes to match the count you set on the dashboard |
| **Launcher** (`launch.py`) | Starts the dashboard, supervisor, and conflict checker together |
| **Post-processing** (`generate_person_ids.py`, `court_bulk_exporter.py`) | De-duplicates defendants and exports the data to CSV/S3 |

**How collection flows:** you create *tasks* (a court + case type + date range) →
*collectors* claim tasks and scrape them → results land in PostgreSQL → the
dashboard reads it all back live.

---

## Prerequisites

- **Python 3** (developed on 3.14; anything 3.10+ should work). The old "Python 2.7"
  instruction in the legacy README no longer applies.
- **PostgreSQL** with the **PostGIS** extension (local, RDS, or any reachable host).
- **Google Chrome** installed on any machine that runs collectors or the
  `load_courts_to_db.py` script. Chrome is required because:
  - the circuit-court site is behind a Cloudflare challenge that is solved with a
    real headless browser (see [Anti-bot handling](#anti-bot-handling)), and
  - `load_courts_to_db.py` needs a browser to get past a one-time CAPTCHA.
- **chromedriver** matching your installed Chrome version, placed in the repo root
  (`chromedriver.exe` on Windows). One is already committed here — replace it if
  your Chrome version differs. (If it's missing/mismatched, Selenium Manager will
  try to auto-download a matching one.)

---

## Setup

### 1. Clone and enter the repo

```bash
git clone https://github.com/LiamTompkins/va-court-scraper.git
cd va-court-scraper
```

### 2. Create a virtual environment

**Windows (PowerShell):**

```powershell
pip install virtualenv
virtualenv venv
.\venv\Scripts\activate.ps1
```

**Linux/macOS:**

```bash
pip install virtualenv
virtualenv venv
source venv/bin/activate
```

### 3. Install dependencies

Everything is pinned in `requirements.txt` (Flask, SQLAlchemy/GeoAlchemy2,
Selenium, `curl_cffi`, `openpyxl`, `rapidfuzz`, `Authlib`, etc.):

```bash
pip install -r requirements.txt
```

> If `psycopg2` fails to build, `requirements.txt` uses `psycopg2-binary`, which
> ships prebuilt wheels — no compiler needed.

### 4. Add chromedriver

Confirm `chromedriver.exe` is in the repo root and its version matches your
installed Chrome (`chrome://version`). Download a match from the
[Chrome for Testing](https://googlechromelabs.github.io/chrome-for-testing/)
list if needed.

### 5. Create the database and enable PostGIS

Connect to your PostgreSQL instance and run once:

```sql
CREATE EXTENSION postgis;
```

### 6. Set the database connection string

The whole system reads one environment variable, `POSTGRES_DB`, in the form
`user:password@host:port/dbname`:

**Windows (PowerShell):**

```powershell
$env:POSTGRES_DB="<user>:<password>@<host>:5432/<dbname>"
```

**Linux/macOS:**

```bash
export POSTGRES_DB='<user>:<password>@<host>:5432/<dbname>'
```

### 7. Initialize the list of courts

Populates the database with every court from the state website. This step opens a
browser so you can solve a one-time CAPTCHA:

```bash
python load_courts_to_db.py
```

---

## Running it

You can run everything at once with the launcher, or start pieces individually.

### Option A — start everything with `launch.py` (recommended)

`launch.py` starts the dashboard, the worker supervisor, and the PracticePanther
conflict checker against the same database, and shuts them all down together on
Ctrl+C:

```powershell
$env:POSTGRES_DB="<user>:<pass>@<host>:5432/<db>"; python launch.py
```

- Dashboard → `http://localhost:5000` (opens automatically)
- Conflict checker → `https://localhost:5001` (adhoc HTTPS)
- `python launch.py --check` prints the resolved configuration without starting
  anything.

### Option B — start the dashboard alone

```bash
python worker_dashboard.py
```

It opens your browser to the dashboard and refreshes live. For each court type it
shows the running workers (with each one's last heartbeat, flagging any that have
gone stale), the pending task count, dates searched, and total cases collected.
While workers are active the totals are fast estimates (prefixed `~`); once a
court goes idle the exact count is shown.

---

## Creating workers (with a button)

**Workers are started from the dashboard — you don't launch collectors by hand.**

1. Make sure a **supervisor is running** on each machine that should host
   collectors:

   ```bash
   python worker_supervisor.py
   ```

   (If you used `launch.py`, a supervisor is already running.)

2. On the dashboard, each court card has a **worker control** showing how many
   collectors are running and a **desired count**. Use the **`+` / `-` buttons**
   to raise or lower that number.

That's it. The dashboard only writes the desired number to the database — it never
spawns processes itself. The supervisor polls that number and starts or stops
local `court_bulk_collector.py` processes to match (capped at **10**, because the
court site becomes unstable past that). Collectors run windowless; each one's
output goes to its own file under `worker_logs/`.

- **Scale across servers** by running one supervisor per machine. The desired
  count is shared through the database, and the dashboard's "running" figure
  reflects the collectors that have actually registered.
- The **Worker logs** button (top of the dashboard) opens a live viewer for the
  `worker_logs/` files on the dashboard's own host.
- A worker that stops sending heartbeats is dropped from the dashboard after
  **1 minute**.

---

## Scheduling collection tasks

A *task* is a locality (FIPS) + court level + case type + descending date range.
Create them either way:

**From the dashboard:** use the **Schedule tasks** form at the top. Pick a court
level (district/circuit), case type (civil/criminal), and a date range with the
start date on or after the end date. Leave FIPS blank to create a task for every
court, or enter one to target a single court. The **Scheduled tasks** box lists
what's queued, newest first, and empties as collectors claim tasks.

**From the command line** (equivalent):

```bash
# <end_date> <start_date> <district|circuit> <civil|criminal> [fips]
python court_bulk_task_creator.py 6/6/2017 6/5/2017 district criminal
```

Once tasks exist and workers are running, collection starts automatically.

### Advanced: run collectors and the watchdog by hand

If you don't use the supervisor/dashboard, you can run collectors directly (court
level as the argument), one process per worker:

```bash
python court_bulk_collector.py district
python court_bulk_collector.py circuit
```

In that manual mode, also run **one** task watchdog to reclaim tasks from crashed
collectors:

```bash
python task_watchdog.py
```

> **Only ever run one task reclaimer.** The supervisor already reclaims stale
> tasks every cycle, so **don't** run `task_watchdog.py` when a supervisor is
> running — two reclaimers at once can create duplicate pending tasks.

---

## Anti-bot handling

The circuit-court site fingerprints the network connection (TLS/JA3 and HTTP/2)
and guards its search entry point with a Cloudflare JavaScript challenge, so a
plain Python HTTP client gets flagged as a bot. The scraper handles this
automatically in `courtreader/`:

- **Fingerprint:** requests go through `curl_cffi`, which presents a real Chrome
  TLS + HTTP/2 fingerprint. (If `curl_cffi` isn't installed it falls back to the
  old `mechanize` client.)
- **Challenge:** when Cloudflare returns its challenge, a headless Chrome solves
  it, and the resulting `cf_clearance` cookie is reused over the fast connection.

This is on by default and needs no configuration beyond having Chrome installed.
Two environment variables tune it:

| Variable | Default | Effect |
| --- | --- | --- |
| `CF_AUTOSOLVE` | `1` | Set to `0` to disable automatic challenge solving |
| `CF_SOLVE_HEADLESS` | `1` | Set to `0` to show the browser while it solves (useful for debugging) |

> If circuit collection starts getting challenged again, first check that Chrome
> and `chromedriver` are up to date; a Chrome-version bump can also be reflected
> in `CHROME_UA` in `courtreader/cf_solver.py`.

---

## Browsing and exporting collected data

The dashboard's **Collected data** page (`/data`) lists each collection *batch*.
Expand a batch to browse its cases in a table that reads live from the database.
You can:

- **Sort** by any column (case id, case number, category, case type, judgement,
  FIPS, date, parties, attorneys).
- **Filter** the cases by case type and judgement (applied server-side, so it
  spans every page).
- **Export** the batch to **Excel** or **CSV** — the current sort and filters are
  carried into the download.

---

## PracticePanther conflict checking

The dashboard can check a batch of collected cases for conflicts of interest
against VPLC's client list in
[PracticePanther](https://www.practicepanther.com/). The PracticePanther
connection is handled by the bundled `elh-conflict-checker/` app, which shares
this project's database; the dashboard is the setup hub and reads the synced
contacts (`pp_contact`) to match batch case parties against them. Name matching
uses `rapidfuzz`; the PracticePanther OAuth sync uses `Authlib`.

### One-time setup

Open the **PracticePanther** link in the dashboard's top nav (or `/pp`), enter the
four values, and **Save**:

1. **PracticePanther Client ID and Client secret** — request API access from
   PracticePanther (**Support → Ask us Anything**), then **Integrations → API →
   New App**.
2. **Login email and password** — these create the conflict checker's sign-in
   account.

Then click **Connect**, sign in with that email/password, and complete the
PracticePanther authorization. The token is stored in the shared database and
drives the **Client contacts** sync into `pp_contact`.

> With the dashboard's own login removed, `/pp` is unauthenticated — anyone who
> can reach the dashboard can set these credentials. Keep the dashboard on
> localhost or a trusted network.

### Running a check

On the **Collected data** page, expand a batch and use:

- **Check client conflicts** — a fuzzy, order-insensitive scan (court
  "LAST, FIRST" matches PracticePanther "First Last") flagging exact and near
  matches.
- **Quick check** — an exact-match-only check, run as a single fast database join.

Matches show the case, the matched party and role, and the VPLC client (with an
"adverse" tag where relevant), and can be exported to Excel or CSV. Results
persist if you navigate away and come back.

---

## Generating person IDs

Grouping criminal cases by defendant is useful but the state provides no unique
identifier, so `generate_person_ids.py` builds one. It buckets cases by gender,
day of birth, and first letter of last name, then fuzzy-matches every name pair
within a bucket. It's CPU-heavy and built to run in parallel — one process per
month. Use a machine with 4+ CPUs.

Prepare the schema, then run all twelve months:

```bash
python generate_person_ids.py 0            # prepare the database
python generate_person_ids.py 1            # month 1
# ... through ...
python generate_person_ids.py 12           # month 12
```

On Linux you can background them:

```bash
for m in $(seq 1 12); do
  nohup python generate_person_ids.py $m >> gen_$m.out 2>&1 &
done
```

Afterward, add a few indexes:

```sql
CREATE INDEX ON person_ids (person_id);
CREATE INDEX ON person_ids (circuit_id);
CREATE INDEX ON person_ids (district_id);
```

---

## Exporting to CSV / S3 / Firebase

`court_bulk_exporter.py` exports the data from Postgres to CSV (by court type and
most-recent-hearing year, then by person id), splits it into ≤250,000-row files,
zips them, uploads to an S3 bucket, and writes file metadata to Firebase.

Vacuum the database before and after:

```sql
VACUUM (VERBOSE, ANALYZE);
```

Required environment (in addition to `POSTGRES_DB`): `FIREBASE_TOKEN`, the
`PG*` variables, and AWS credentials via `aws configure`. Then:

```bash
nohup python court_bulk_exporter.py >> export.out 2>&1 &
```

---

## Environment variables reference

| Variable | Required | Purpose |
| --- | --- | --- |
| `POSTGRES_DB` | Yes | Database connection: `user:pass@host:port/dbname` |
| `CF_AUTOSOLVE` | No (default `1`) | Auto-solve Cloudflare challenges |
| `CF_SOLVE_HEADLESS` | No (default `1`) | Solve headless (`0` shows the browser) |
| `SECRET_KEY` | No | Conflict checker session secret (generated if unset) |
| `DASHBOARD_PORT` / `CONFLICT_CHECKER_PORT` | No | Ports (defaults 5000 / 5001) |
| `CONFLICT_CHECKER_DIR` / `CONFLICT_CHECKER_PYTHON` / `DASHBOARD_PYTHON` | No | Point `launch.py` at a specific checkout / interpreter |
| `TASK_MANAGER` | No | `supervisor` (default), `watchdog`, or `none` |
| `FIREBASE_TOKEN`, `PGHOST`/`PGDATABASE`/`PGUSER`/`PGPASSWORD` | Export only | Used by `court_bulk_exporter.py` |

---

## Quick start (TL;DR)

```bash
# 1. Setup
git clone https://github.com/LiamTompkins/va-court-scraper.git
cd va-court-scraper
virtualenv venv && .\venv\Scripts\activate.ps1        # (source venv/bin/activate on Linux/macOS)
pip install -r requirements.txt
# ensure chromedriver.exe matches your Chrome; PostGIS enabled on the DB

# 2. Configure + initialize
$env:POSTGRES_DB="<user>:<pass>@<host>:5432/<db>"
python load_courts_to_db.py                            # solve the one-time CAPTCHA

# 3. Run
python launch.py                                       # dashboard + supervisor + conflict checker
```

Then, in the dashboard: **Schedule tasks** for a court/date range, and press the
**`+` button** on a court card to start workers.
