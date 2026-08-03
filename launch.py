"""Start the worker dashboard and the elh-conflict-checker together.

The dashboard's "Connect" button opens the conflict checker's sign-in page, so
the conflict checker has to be running. This launcher starts both, wires them to
the same PostgreSQL database, and shuts both down together (Ctrl+C). It only
orchestrates local subprocesses -- for a cloud/multi-server setup, run each app
under its own service manager instead.

Usage:
    python launch.py            # start both apps
    python launch.py --check    # print the resolved config and exit

Configuration (environment variables, all optional except POSTGRES_DB):
    POSTGRES_DB            shared DB as user:pass@host:port/dbname (required)
    SECRET_KEY            conflict checker session secret (generated if unset)
    DASHBOARD_PORT        default 5000
    CONFLICT_CHECKER_PORT default 5001
    CONFLICT_CHECKER_DIR  path to the conflict checker repo
                          (default: ../../elh-conflict-checker)
    CONFLICT_CHECKER_PYTHON / DASHBOARD_PYTHON
                          override the interpreter used for each app
"""
from __future__ import absolute_import
from __future__ import print_function
import os
import sys
import time
import secrets
import threading
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))


def venv_python(base):
    """The venv interpreter inside base, or None."""
    for rel in ('venv/Scripts/python.exe', 'venv/bin/python',
                '.venv/Scripts/python.exe', '.venv/bin/python'):
        path = os.path.join(base, rel)
        if os.path.isfile(path):
            return path
    return None


def resolve_config():
    postgres_db = os.environ.get('POSTGRES_DB', '')
    dash_port = os.environ.get('DASHBOARD_PORT', '5000')
    cc_port = os.environ.get('CONFLICT_CHECKER_PORT', '5001')

    cc_dir = os.environ.get('CONFLICT_CHECKER_DIR') or \
        os.path.normpath(os.path.join(HERE, '..', '..', 'elh-conflict-checker'))
    dash_py = os.environ.get('DASHBOARD_PYTHON') or venv_python(HERE) or sys.executable
    cc_py = os.environ.get('CONFLICT_CHECKER_PYTHON') or venv_python(cc_dir) or sys.executable

    return {
        'postgres_db': postgres_db,
        'dash_port': dash_port,
        'cc_port': cc_port,
        'cc_dir': cc_dir,
        'dash_py': dash_py,
        'cc_py': cc_py,
        # The conflict checker wants a full SQLAlchemy URL; the dashboard wants
        # the bare user:pass@host/db. Derive one from the other.
        'database_url': 'postgresql+psycopg://' + postgres_db if postgres_db else '',
        'secret_key': os.environ.get('SECRET_KEY') or secrets.token_urlsafe(32),
    }


def validate(cfg):
    problems = []
    if not cfg['postgres_db']:
        problems.append('POSTGRES_DB is not set (shared database connection string).')
    if not os.path.isdir(cfg['cc_dir']):
        problems.append('Conflict checker directory not found: %s '
                        '(set CONFLICT_CHECKER_DIR).' % cfg['cc_dir'])
    elif not os.path.isfile(os.path.join(cfg['cc_dir'], 'app.py')):
        problems.append('No app.py in the conflict checker directory: %s' % cfg['cc_dir'])
    if not os.path.isfile(cfg['cc_py']):
        problems.append('Conflict checker interpreter not found: %s '
                        '(set CONFLICT_CHECKER_PYTHON).' % cfg['cc_py'])
    return problems


def stream(proc, label):
    """Forward a child's output, line by line, with a label prefix."""
    for line in iter(proc.stdout.readline, ''):
        sys.stdout.write('[%s] %s' % (label, line))
        sys.stdout.flush()
    proc.stdout.close()


def start(cmd, cwd, env, label):
    print('Starting %s: %s' % (label, ' '.join(cmd)))
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        universal_newlines=True, bufsize=1)
    threading.Thread(target=stream, args=(proc, label), daemon=True).start()
    return proc


def main():
    cfg = resolve_config()
    checking = '--check' in sys.argv[1:]

    print('Resolved configuration:')
    print('  Dashboard        : %s (port %s)' % (cfg['dash_py'], cfg['dash_port']))
    print('  Conflict checker : %s (port %s)' % (cfg['cc_py'], cfg['cc_port']))
    print('  Conflict dir     : %s' % cfg['cc_dir'])
    print('  Shared database  : %s' % ('set' if cfg['postgres_db'] else 'NOT SET'))
    print('')

    problems = validate(cfg)
    if problems:
        print('Cannot start:')
        for p in problems:
            print('  - ' + p)
        return 1
    if checking:
        print('Config OK.')
        return 0

    cc_url = 'https://127.0.0.1:%s' % cfg['cc_port']

    dash_env = os.environ.copy()
    dash_env['POSTGRES_DB'] = cfg['postgres_db']
    dash_env['DASHBOARD_PORT'] = cfg['dash_port']
    dash_env['CONFLICT_CHECKER_URL'] = cc_url

    cc_env = os.environ.copy()
    cc_env['SECRET_KEY'] = cfg['secret_key']
    cc_env['DATABASE_URL'] = cfg['database_url']
    cc_env['FLASK_APP'] = 'app.py'

    procs = []
    try:
        procs.append(start([cfg['cc_py'], '-m', 'flask', 'run', '--cert=adhoc',
                            '--host=127.0.0.1', '--port', cfg['cc_port']],
                           cfg['cc_dir'], cc_env, 'checker'))
        # Give the conflict checker a moment so its port is up when the dashboard
        # opens the browser.
        time.sleep(2)
        procs.append(start([cfg['dash_py'], 'worker_dashboard.py'],
                           HERE, dash_env, 'dashboard'))

        print('\nBoth running. Dashboard: http://127.0.0.1:%s   '
              'Conflict checker: %s\nPress Ctrl+C to stop both.\n'
              % (cfg['dash_port'], cc_url))

        # Wait until either exits, then take the other down too.
        while True:
            for p in procs:
                if p.poll() is not None:
                    print('\nA process exited (code %s); shutting down the other.'
                          % p.returncode)
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        print('\nStopping...')
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except Exception:
                p.kill()
    return 0


if __name__ == '__main__':
    sys.exit(main())
