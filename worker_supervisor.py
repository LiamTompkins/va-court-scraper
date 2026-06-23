from __future__ import absolute_import
from __future__ import print_function
import os
import sys
import time
import socket
import subprocess
from datetime import datetime

from sqlalchemy import create_engine, text

# Runs on each machine that should host collectors. Polls the worker_targets
# table (set from the dashboard) and starts/stops local court_bulk_collector.py
# processes so the number running matches the desired count. The dashboard never
# spawns processes itself - it only writes the desired number here.

COURT_TYPES = ['district', 'circuit']
POLL_SECONDS = 5
MAX_WORKERS = 10  # the court site becomes unstable past ~10 collectors

engine = create_engine('postgresql://' + os.environ['POSTGRES_DB'])

# Child collector processes we started, keyed by court type.
children = {ct: [] for ct in COURT_TYPES}


def ensure_target_table():
    try:
        with engine.begin() as conn:
            conn.execute(text(
                'CREATE TABLE IF NOT EXISTS worker_targets ('
                ' court_type VARCHAR PRIMARY KEY, desired_count INTEGER, updated_at TIMESTAMP)'
            ))
    except Exception:
        pass


def get_desired(court_type):
    try:
        with engine.connect() as conn:
            val = conn.execute(text(
                'SELECT desired_count FROM worker_targets WHERE court_type = :ct'
            ), {'ct': court_type}).scalar()
        return max(0, min(MAX_WORKERS, int(val))) if val is not None else 0
    except Exception:
        return None  # DB unreachable - leave things as they are


def reap(court_type):
    children[court_type] = [p for p in children[court_type] if p.poll() is None]


def start_worker(court_type):
    kwargs = {}
    if os.name == 'nt':
        # Give each collector its own console window so its logs stay visible.
        kwargs['creationflags'] = subprocess.CREATE_NEW_CONSOLE
    p = subprocess.Popen([sys.executable, 'court_bulk_collector.py', court_type], **kwargs)
    children[court_type].append(p)
    print('[%s] started %s collector pid=%d' % (
        datetime.now().strftime('%H:%M:%S'), court_type, p.pid))


def stop_worker(court_type):
    if children[court_type]:
        p = children[court_type].pop()
        try:
            p.terminate()
            print('[%s] stopped %s collector pid=%d' % (
                datetime.now().strftime('%H:%M:%S'), court_type, p.pid))
        except Exception:
            pass


def main():
    ensure_target_table()
    print('Supervisor running on %s (poll every %ds)' % (socket.gethostname(), POLL_SECONDS))
    while True:
        for court_type in COURT_TYPES:
            reap(court_type)
            desired = get_desired(court_type)
            if desired is None:
                continue
            running = len(children[court_type])
            while running < desired:
                start_worker(court_type)
                running += 1
            while running > desired:
                stop_worker(court_type)
                running -= 1
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
