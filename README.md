# Virginia Court Scraper

This tool is designed to collect court case information published on [Virginia's district and circuit court case information websites](http://www.courts.state.va.us/caseinfo/). Data collected by the scraper can be downloaded at [VirginiaCourtData.org](http://virginiacourtdata.org).

## How to run the scraper

### Environment

I'll be using AWS, but that's not a requirement.

1. Create an EC2 instance. You'll need something than can run Google Chrome, so I'm using Windows
1. Create an RDS PostrgreSQL instance
1. Make sure your security groups allow your server to connect to the database
1. Connect to the server using RDP
1. Install Google Chrome, git, and Python 2.7
1. Install [pyscopg2](http://www.stickpeople.com/projects/python/win-psycopg/)
1. Clone this repository

        git clone https://github.com/bschoenfeld/va-court-scraper.git

1. Download [Chromedriver](https://sites.google.com/a/chromium.org/chromedriver/downloads) to the root of the repo directory
1. Create a virtual environment in the root of the repo directory. Again, I'm using powershell on windows. The commands will be a bit different for activating the virtual environment and setting the environment variables

        pip install virtualenv
        virtualenv venv
        .\venv\Scripts\activate.ps1

1. Install dependencies

        pip install selenium mechanize beautifulsoup4 psycopg2 SQLAlchemy GeoAlchemy2 pgcli requests

1. Connect to the database and add the postgis extension

        pgcli postgres://<PGUSER>:<PGPASSWORD>@<PGHOST>:<PGPORT>/<PGDATABASE>
        CREATE EXTENSION postgis;
        \q

1. Set environment variables

        $env:POSTGRES_DB="<PGUSER>:<PGPASSWORD>@<PGHOST>:<PGPORT>/<PGDATABASE>"

### Initalize database with list of courts

Running this script will populate the database with the list of courts from the state website.

        python load_courts_to_db.py

### Create tasks

First, create data collection tasks. Tasks are made up of a locality, court level, case type, and date range. Run this script to create tasks. The parameters are ending date, starting date, court level (district or circuit), case type (civil or criminal), and optionally, court fips. If court fips is left out, a task will be created for every court.

        python court_bulk_task_creator.py 6/6/2017 6/5/2017 district criminal

### Collect cases

Now you can create collectors. When a collector runs, it will take a task and start collecting data. You must specify the court level (district or circuit) as a parameter. You can run mulitple collectors at once, but in my experience, the website becomes unstable when running more than 10 collectors in parallel.

        python court_bulk_collector.py district

### Run the task watchdog

The task watchdog prevents tasks from being permanently lost if a collector crashes mid-run. It monitors active tasks and automatically resets any that have not sent a heartbeat in over 2 minutes back to pending, so another worker can pick them up.

Run it in a separate terminal alongside your collectors:

        python task_watchdog.py

Only one instance of the watchdog needs to run regardless of how many collectors you have. It is recommended to always run the watchdog when running collectors.

**If you manage collectors with the supervisor (see below), you do not need the watchdog** - the supervisor already reclaims stale tasks on every poll cycle. Only run the watchdog when you start collectors manually (without the supervisor), and never run both at once, since two processes reclaiming stale tasks concurrently can create duplicate pending rows.

### Monitor workers with the dashboard

The dashboard gives a live, browser-based view of what the collectors are doing, and lets you schedule new collection tasks. Start it in its own terminal:

        python worker_dashboard.py

It automatically opens your browser to the dashboard and refreshes every second. For each court type it shows the active workers (with how long ago each one last sent a heartbeat, highlighting any that have gone stale), the number of pending tasks, how many dates have been searched, and the total cases collected. While workers are active, case totals are shown as fast approximate estimates (prefixed with `~`); once a court's workers are idle, the exact count is computed and shown.

The "Schedule tasks" form at the top creates collection tasks without the command line - the same as running `court_bulk_task_creator.py`. Choose a court level, case type, and a descending date range (start date on or after end date); leave FIPS blank to create a task for every court, or enter one to target a single court. Newly created tasks appear in the pending count and are picked up by any running collectors.

The "Scheduled tasks" box lists the tasks currently queued (court, FIPS, case type, and date range), paginated newest-first. It updates live, so tasks disappear from the list as collectors claim them.

The dashboard runs on Python 3 and imports `Flask`, `rapidfuzz`, and `Authlib` at startup, so run it from a Python 3 virtual environment with `pip install -r requirements.txt`. The Excel export of collected data also uses `openpyxl`, which is included there.

### Start workers from the dashboard

Each court card has a worker control showing how many collectors are running and a desired count you can raise or lower with the `+` / `-` buttons. The dashboard only writes the desired number to the database - it never spawns processes itself. A supervisor process does the spawning, so this works the same whether you run everything on one machine or across several servers.

On every machine that should host collectors, run the supervisor:

        python worker_supervisor.py

The supervisor polls the desired count and starts or stops local `court_bulk_collector.py` processes to match (capped at 10, since the court site becomes unstable past that). Collectors run without opening a window; each one's output is written to its own file under `worker_logs/`. To scale across servers, run one supervisor per machine - the desired count is shared through the database, and the dashboard's "running" figure reflects the actual collectors that have registered.

The supervisor also reclaims stale tasks each cycle (the same job as the watchdog), so when you run the supervisor you do not need to run `task_watchdog.py` as well.

The **Worker logs** button at the top opens a viewer for those `worker_logs/` files: pick a log on the left to see its tail on the right, refreshed live while open. Note that the dashboard reads the log directory on its own machine, so it shows logs for collectors running on that same host (in a multi-server setup, logs on other machines aren't visible here).

## PracticePanther conflict checking

The dashboard can check a batch of collected cases for conflicts of interest against VPLC's client list in [PracticePanther](https://www.practicepanther.com/). The actual PracticePanther connection is handled by the separate [elh-conflict-checker](elh-conflict-checker/) app (a copy is vendored in this repo); the dashboard is a setup hub that shares its database. The dashboard reads the synced client contacts (`pp_contact`) and matches batch case parties against them.

The name matching uses `rapidfuzz` and the PracticePanther sync uses `Authlib`, both listed in `requirements.txt`.

### Running everything together

The dashboard's **Connect** button opens the conflict checker's sign-in page, and collecting cases needs a task manager, so several processes have to run against the same database. `launch.py` starts them together and shuts them all down on Ctrl+C:

        $env:POSTGRES_DB="<user>:<pass>@<host>:5432/<db>"; python launch.py

It runs the dashboard (port 5000), the conflict checker (port 5001, adhoc HTTPS), and the worker supervisor - passing the shared database to each. `python launch.py --check` prints the resolved configuration without starting anything. Useful environment variables:

- `POSTGRES_DB` - shared database as `user:pass@host:port/dbname` (required).
- `SECRET_KEY` - conflict checker session secret (generated if unset).
- `DASHBOARD_PORT` / `CONFLICT_CHECKER_PORT` - defaults 5000 / 5001.
- `CONFLICT_CHECKER_DIR` - path to a conflict checker checkout that has its own venv; the default `../../elh-conflict-checker` points at a sibling clone. The `elh-conflict-checker/` copy vendored in this repo is source only (no venv), so point at it only after creating a venv there. `CONFLICT_CHECKER_PYTHON` / `DASHBOARD_PYTHON` override the interpreters.
- `TASK_MANAGER` - `supervisor` (default; spawns collectors to match the desired worker count and reclaims stale tasks), `watchdog` (reclaim stale tasks only), or `none`. Only one runs - never both, since they would both reclaim stale tasks and create duplicate pending rows. The supervisor spawns collectors only when you raise the desired worker count on the dashboard, so it is idle otherwise.

If any of the launched processes exits, `launch.py` stops the rest, so a crash doesn't leave orphans.

To run them separately instead, start the conflict checker with `python -m flask run --cert=adhoc --host=127.0.0.1 --port=5001` (from its directory, with `SECRET_KEY` and `DATABASE_URL` set) and the dashboard with `python worker_dashboard.py`; set `CONFLICT_CHECKER_URL` on the dashboard if the checker isn't at `https://127.0.0.1:5001`.

### Setup (the PracticePanther page)

Open the **PracticePanther** link in the dashboard's top nav (or go to `/pp`). Enter the four setup values and Save:

1. **PracticePanther Client ID and Client secret.** PracticePanther grants API access case by case - click **Support -> Ask us Anything** and request it, then once approved go to **Integrations -> API -> New App**. These are saved in the shared database and read by the conflict checker (the `PP_CLIENT_ID` / `PP_CLIENT_SECRET` environment variables are a fallback).
2. **Login email and password.** These create the conflict checker's sign-in account (stored in the shared `user` table).

Then click **Connect**. It opens the conflict checker's sign-in page; log in with the email/password above, and do the PracticePanther authorization there. The conflict checker uses the client id/secret you saved, and stores the token in the shared database. Back on the dashboard, that token drives the **Client contacts** sync into the shared `pp_contact` table (the list the conflict check matches against).

> Note: with the dashboard's own login removed, the `/pp` setup page is unauthenticated - anyone who can reach the dashboard can set the credentials and the conflict checker login. Keep the dashboard on localhost or a trusted network.

### Running the conflict check

On the **Collected data** page (`/data`), expand a batch and use:

- **Check client conflicts** - a fuzzy scan (order-insensitive, so court "LAST, FIRST" matches PracticePanther "First Last") that flags exact and near matches for review.
- **Quick check** - an exact-match-only check, run as a single database join for speed.

Matches list the case, the matched party and its role, and the VPLC client (with an "adverse" tag where relevant). Results can be downloaded as Excel or CSV - each row includes the full collected-data record for the case - and they persist if you leave the page and come back.

## How to generate person ids

Many effective uses of this data require grouping criminal cases to defendant. Unfortunately, the state does not provide any unique identifier, so the [generate_person_ids.py](https://github.com/bschoenfeld/va-court-scraper/blob/master/generate_person_ids.py) script attempts to create one. The script takes all cases and breaks them into groups based on gender, day of birth (there are no years in the case data), and first letter of last name. For each group, every name is compared to every other name using a fuzzy string match. This process can take a while. The script is built so that it can be run in parallel, one execution for each month of the year. I recommend a beefy server - I use a t2.xlarge on AWS, which has 4 CPUs and 16 GB of memory.

Get your environment ready to connect to psql (the commands for that are elsewhere in the README, install some additional libraries

```
pip install fuzzywuzzy
pip install python-Levenshtein
```

Then prepare the database

```
python generate_person_ids.py 0
```

Finally run the script for all 12 months

```
nohup python generate_person_ids.py 1 >> gen_1.out 2>&1 &
nohup python generate_person_ids.py 2 >> gen_2.out 2>&1 &
nohup python generate_person_ids.py 3 >> gen_3.out 2>&1 &
nohup python generate_person_ids.py 4 >> gen_4.out 2>&1 &
nohup python generate_person_ids.py 5 >> gen_5.out 2>&1 &
nohup python generate_person_ids.py 6 >> gen_6.out 2>&1 &
nohup python generate_person_ids.py 7 >> gen_7.out 2>&1 &
nohup python generate_person_ids.py 8 >> gen_8.out 2>&1 &
nohup python generate_person_ids.py 9 >> gen_9.out 2>&1 &
nohup python generate_person_ids.py 10 >> gen_10.out 2>&1 &
nohup python generate_person_ids.py 11 >> gen_11.out 2>&1 &
nohup python generate_person_ids.py 12 >> gen_12.out 2>&1 &
```

After it runs, I create a few indexes

```
vacourtscraper=> create index on person_ids (person_id);
CREATE INDEX
vacourtscraper=> create index on person_ids (circuit_id);
CREATE INDEX
vacourtscraper=> create index on person_ids (district_id);
CREATE INDEX
```

## How to run the export

The export script exports data from Postgres to CSV files. The data are exported first by court type and year of most recent hearing, and then by person id. The script uses the psql subprocess to run the copy command to download large chunks of data to the local machine. Then the script breaks the CSVs up so that no file has more than 250,000 cases. Finally, the CSVs are zipped up and pushed to an AWS S3 bucket. Once the script has uploaded all the zip files, it generates a bunch of metadata about the files (number of cases, file size, S3 path) and pushes that metadata to a Firebase database.

Be sure to connect to the database using psql and vacuum it before and after the export.

```
VACUUM (VERBOSE, ANALYZE);
```

Start an Amazon Linux EC2 instance. SSH and run the following commands.

```
sudo yum update
sudo yum -y install gcc gcc-c++ make
sudo yum -y install postgresql postgresql-server postgresql-devel postgresql-contrib postgresql-docs
sudo yum -y install git
git clone https://github.com/bschoenfeld/va-court-scraper.git
cd va-court-scraper
virtualenv venv
source venv/bin/activate
pip install selenium mechanize beautifulsoup4 psycopg2 SQLAlchemy GeoAlchemy2 pgcli boto3 awscli python-firebase requests
export FIREBASE_TOKEN='<FIREBASETOKEN>'
export PGHOST='<PGHOST>'
export PGDATABASE='<PGDATABASE>'
export PGUSER='<PGUSER>'
export PGPASSWORD='<PGPASSWORD>'
export POSTGRES_DB='<PGUSER>:<PGPASSWORD>@<PGHOST>:<PGPORT>/<PGDATABASE>'
```

Run `psql` to make sure you can connect to the instance. Type `\q` to disconnect.  
Run `aws configure` to set up your connection to AWS. Confirm everthing is setup by running `aws s3 ls`.  
Run the script  

```
nohup python court_bulk_exporter.py >> export.out 2>&1 &
```
