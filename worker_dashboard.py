from __future__ import absolute_import
from __future__ import print_function
import os
import threading
import webbrowser
from datetime import datetime

from flask import Flask, jsonify, request
from sqlalchemy import create_engine, text

# Seconds without a heartbeat before an active task is considered stale.
# Matches the threshold used by task_watchdog.py.
STALE_THRESHOLD_SECONDS = 120
PORT = 5000

app = Flask(__name__)
engine = create_engine("postgresql://" + os.environ['POSTGRES_DB'])

# Case tables are created by SQLAlchemy with mixed-case names. CASE_TABLE_NAMES
# holds the raw name (for pg_class lookups); queries that hit the table directly
# must double-quote it.
CASE_TABLE_NAMES = {
    'circuit': {'criminal': 'CircuitCriminalCase', 'civil': 'CircuitCivilCase'},
    'district': {'criminal': 'DistrictCriminalCase', 'civil': 'DistrictCivilCase'},
}

# Cache of exact case counts, keyed by (court_type, case_type). Only used while a
# court has no active workers, where the count cannot change, so it stays valid
# until workers run again.
_exact_count_cache = {}


def scalar(conn, sql):
    """Run a COUNT-style query, returning 0 if the table doesn't exist yet."""
    try:
        return conn.execute(text(sql)).scalar() or 0
    except Exception:
        return 0


def case_count(conn, court_type, case_type, has_active_workers):
    """Return the case count for a table.

    While workers are active, use PostgreSQL's instant approximate row estimate
    (pg_class.reltuples) so the 1s refresh never scans the table. Once the court
    is idle, compute the exact COUNT(*) once and cache it (the count can't change
    with no workers running) so we still don't rescan every second.
    """
    table = CASE_TABLE_NAMES[court_type][case_type]
    key = (court_type, case_type)

    if has_active_workers:
        _exact_count_cache.pop(key, None)
        try:
            val = conn.execute(
                text('SELECT reltuples::bigint FROM pg_class WHERE relname = :n'),
                {'n': table}
            ).scalar()
            val = int(val) if val and val > 0 else 0
        except Exception:
            val = 0
        return {'count': val, 'approximate': True}

    if key not in _exact_count_cache:
        _exact_count_cache[key] = scalar(conn, 'SELECT COUNT(*) FROM "%s"' % table)
    return {'count': _exact_count_cache[key], 'approximate': False}


def collect_court_status(conn, court_type):
    active_table = '%s_court_active_date_tasks' % court_type
    pending_table = '%s_court_date_tasks' % court_type
    searched_table = '%s_court_dates_searched' % court_type

    active = []
    try:
        rows = conn.execute(text(
            'SELECT fips, casetype, startdate, enddate, last_alive '
            'FROM %s ORDER BY last_alive DESC NULLS LAST' % active_table
        ))
        now = datetime.now()
        for r in rows:
            last_alive = r[4]
            seconds_since = None
            stale = True
            if last_alive is not None:
                seconds_since = int((now - last_alive).total_seconds())
                stale = seconds_since > STALE_THRESHOLD_SECONDS
            active.append({
                'fips': str(r[0]).zfill(3),
                'case_type': r[1],
                'start_date': r[2].isoformat() if r[2] else None,
                'end_date': r[3].isoformat() if r[3] else None,
                'last_alive': last_alive.isoformat() if last_alive else None,
                'seconds_since': seconds_since,
                'stale': stale,
            })
    except Exception:
        pass

    completed_table = '%s_court_completed_date_tasks' % court_type
    completed = []
    try:
        rows = conn.execute(text(
            'SELECT fips, casetype, startdate, enddate, completed_at '
            'FROM %s ORDER BY completed_at DESC LIMIT 10' % completed_table
        ))
        for r in rows:
            completed.append({
                'fips': str(r[0]).zfill(3),
                'case_type': r[1],
                'start_date': r[2].isoformat() if r[2] else None,
                'end_date': r[3].isoformat() if r[3] else None,
                'completed_at': r[4].isoformat() if r[4] else None,
            })
    except Exception:
        pass

    has_active_workers = len(active) > 0
    return {
        'active': active,
        'completed': completed,
        'completed_count': scalar(conn, 'SELECT COUNT(*) FROM %s' % completed_table),
        'pending_count': scalar(conn, 'SELECT COUNT(*) FROM %s' % pending_table),
        'dates_searched': scalar(conn, 'SELECT COUNT(*) FROM %s' % searched_table),
        'cases': {
            'criminal': case_count(conn, court_type, 'criminal', has_active_workers),
            'civil': case_count(conn, court_type, 'civil', has_active_workers),
        },
    }


@app.route('/api/status')
def status():
    with engine.connect() as conn:
        data = {
            'generated_at': datetime.now().isoformat(),
            'stale_threshold_seconds': STALE_THRESHOLD_SECONDS,
            'courts': {
                'district': collect_court_status(conn, 'district'),
                'circuit': collect_court_status(conn, 'circuit'),
            },
        }
    return jsonify(data)


COMPLETED_PER_PAGE = 10


@app.route('/api/completed')
def completed():
    try:
        page = max(0, int(request.args.get('page', 0)))
    except (TypeError, ValueError):
        page = 0

    rows = []
    with engine.connect() as conn:
        for court_type in ['district', 'circuit']:
            table = '%s_court_completed_date_tasks' % court_type
            try:
                result = conn.execute(text(
                    'SELECT fips, casetype, startdate, enddate, completed_at FROM %s' % table
                ))
                for r in result:
                    rows.append({
                        'court': court_type.capitalize(),
                        'fips': str(r[0]).zfill(3),
                        'case_type': r[1],
                        'start_date': r[2].isoformat() if r[2] else None,
                        'end_date': r[3].isoformat() if r[3] else None,
                        'completed_at': r[4].isoformat() if r[4] else None,
                        '_ts': r[4] or datetime.min,
                    })
            except Exception:
                pass

    rows.sort(key=lambda x: x['_ts'], reverse=True)
    total = len(rows)
    start = page * COMPLETED_PER_PAGE
    page_rows = rows[start:start + COMPLETED_PER_PAGE]
    for r in page_rows:
        del r['_ts']

    return jsonify({
        'page': page,
        'per_page': COMPLETED_PER_PAGE,
        'total': total,
        'tasks': page_rows,
    })


@app.route('/')
def index():
    return PAGE


PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>VA Court Scraper - Worker Dashboard</title>
<style>
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin: 24px; background: #0f1115; color: #e6e6e6; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .meta { color: #8a8f98; font-size: 13px; margin-bottom: 20px; }
  .grid { display: flex; gap: 24px; flex-wrap: wrap; }
  .court { flex: 1; min-width: 420px; background: #171a21; border: 1px solid #262b36; border-radius: 8px; padding: 16px; }
  .court h2 { font-size: 16px; margin: 0 0 12px; text-transform: capitalize; }
  .court h3.section { font-size: 12px; color: #8a8f98; text-transform: uppercase; letter-spacing: .04em; margin: 18px 0 8px; }
  .stats { display: flex; gap: 16px; margin-bottom: 14px; flex-wrap: wrap; }
  .stat { background: #0f1115; border-radius: 6px; padding: 8px 12px; min-width: 90px; }
  .stat .n { font-size: 20px; font-weight: 600; }
  .stat .l { font-size: 11px; color: #8a8f98; text-transform: uppercase; letter-spacing: .04em; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid #262b36; }
  th { color: #8a8f98; font-weight: 500; font-size: 11px; text-transform: uppercase; }
  .ok { color: #3fb950; }
  .stale { color: #f85149; font-weight: 600; }
  .empty { color: #8a8f98; font-style: italic; padding: 8px; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
  .dot.ok { background: #3fb950; }
  .dot.stale { background: #f85149; }
  .pager { display: flex; align-items: center; gap: 12px; margin-top: 12px; font-size: 13px; color: #8a8f98; }
  .pager button { background: #21262d; color: #e6e6e6; border: 1px solid #30363d; border-radius: 6px; padding: 5px 12px; cursor: pointer; }
  .pager button:hover:not([disabled]) { background: #30363d; }
  .pager button[disabled] { opacity: .4; cursor: default; }
</style>
</head>
<body>
  <h1>Virginia Court Scraper &mdash; Worker Dashboard</h1>
  <div class="meta" id="meta">Loading&hellip;</div>
  <div class="grid" id="grid"></div>
  <div class="grid" id="grid-completed" style="margin-top:24px;"></div>
<script>
function fmtAgo(s) {
  if (s === null) return 'no heartbeat';
  if (s < 60) return s + 's ago';
  var m = Math.floor(s / 60);
  return m + 'm ' + (s % 60) + 's ago';
}
function fmtCount(c) {
  // c is {count, approximate}; show ~ while the figure is an estimate
  return (c.approximate ? '~' : '') + c.count.toLocaleString();
}
function fmtWhen(iso) {
  if (!iso) return '';
  var d = new Date(iso);
  var secs = Math.floor((Date.now() - d.getTime()) / 1000);
  return fmtAgo(secs);
}
function courtCard(name, c) {
  var rows = c.active.map(function(t) {
    var cls = t.stale ? 'stale' : 'ok';
    return '<tr>' +
      '<td><span class="dot ' + cls + '"></span>' + t.fips + '</td>' +
      '<td>' + t.case_type + '</td>' +
      '<td>' + (t.start_date || '') + ' → ' + (t.end_date || '') + '</td>' +
      '<td class="' + cls + '">' + fmtAgo(t.seconds_since) + '</td>' +
      '</tr>';
  }).join('');
  var table = c.active.length
    ? '<table><tr><th>FIPS</th><th>Type</th><th>Date range</th><th>Last heartbeat</th></tr>' + rows + '</table>'
    : '<div class="empty">No active workers</div>';

  return '<div class="court">' +
    '<h2>' + name + '</h2>' +
    '<div class="stats">' +
      '<div class="stat"><div class="n">' + c.active.length + '</div><div class="l">Active</div></div>' +
      '<div class="stat"><div class="n">' + c.pending_count + '</div><div class="l">Pending</div></div>' +
      '<div class="stat"><div class="n">' + c.completed_count + '</div><div class="l">Completed</div></div>' +
      '<div class="stat"><div class="n">' + c.dates_searched + '</div><div class="l">Dates done</div></div>' +
      '<div class="stat"><div class="n">' + fmtCount(c.cases.criminal) + '</div><div class="l">Criminal cases</div></div>' +
      '<div class="stat"><div class="n">' + fmtCount(c.cases.civil) + '</div><div class="l">Civil cases</div></div>' +
    '</div>' + table + '</div>';
}
var completedPage = 0;
var lastStatus = null;
function renderCompleted(data) {
  var doneRows = data.tasks.map(function(t) {
    return '<tr>' +
      '<td>' + t.court + '</td>' +
      '<td>' + t.fips + '</td>' +
      '<td>' + t.case_type + '</td>' +
      '<td>' + (t.start_date || '') + ' → ' + (t.end_date || '') + '</td>' +
      '<td>' + fmtWhen(t.completed_at) + '</td>' +
      '</tr>';
  }).join('');
  var doneTable = data.tasks.length
    ? '<table><tr><th>Court</th><th>FIPS</th><th>Type</th><th>Date range</th><th>Completed</th></tr>' + doneRows + '</table>'
    : '<div class="empty">No completed tasks yet</div>';

  var totalPages = Math.max(1, Math.ceil(data.total / data.per_page));
  var page = data.page;
  var prevDisabled = page <= 0 ? 'disabled' : '';
  var nextDisabled = (page + 1) >= totalPages ? 'disabled' : '';
  var pager = '<div class="pager">' +
    '<button onclick="changePage(-1)" ' + prevDisabled + '>&larr; Newer</button>' +
    '<span>Page ' + (page + 1) + ' of ' + totalPages + '</span>' +
    '<button onclick="changePage(1)" ' + nextDisabled + '>Older &rarr;</button>' +
    '</div>';

  var dCount = lastStatus ? lastStatus.courts.district.completed_count : 0;
  var cCount = lastStatus ? lastStatus.courts.circuit.completed_count : 0;
  document.getElementById('grid-completed').innerHTML =
    '<div class="court">' +
    '<h2>Completed tasks</h2>' +
    '<div class="stats">' +
      '<div class="stat"><div class="n">' + data.total + '</div><div class="l">Total completed</div></div>' +
      '<div class="stat"><div class="n">' + dCount + '</div><div class="l">District</div></div>' +
      '<div class="stat"><div class="n">' + cCount + '</div><div class="l">Circuit</div></div>' +
    '</div>' + doneTable + pager + '</div>';
}
function loadCompleted() {
  fetch('/api/completed?page=' + completedPage)
    .then(function(r){ return r.json(); })
    .then(function(data){
      // Clamp if the last page shrank (e.g. fewer tasks than expected)
      var totalPages = Math.max(1, Math.ceil(data.total / data.per_page));
      if (completedPage > totalPages - 1) {
        completedPage = totalPages - 1;
        return loadCompleted();
      }
      renderCompleted(data);
    });
}
function changePage(delta) {
  completedPage = Math.max(0, completedPage + delta);
  loadCompleted();
}
var ACTIVE_INTERVAL = 1000;
var IDLE_INTERVAL = 10000;
function scheduleNext(ms) { setTimeout(refresh, ms); }
function refresh() {
  fetch('/api/status').then(function(r){ return r.json(); }).then(function(d){
    lastStatus = d;
    document.getElementById('grid').innerHTML =
      courtCard('District', d.courts.district) + courtCard('Circuit', d.courts.circuit);
    loadCompleted();
    // Poll fast while any worker is active, slowly when everything is idle
    var anyActive = d.courts.district.active.length + d.courts.circuit.active.length > 0;
    var interval = anyActive ? ACTIVE_INTERVAL : IDLE_INTERVAL;
    document.getElementById('meta').textContent =
      'Updated ' + new Date(d.generated_at).toLocaleTimeString() +
      ' · stale after ' + d.stale_threshold_seconds + 's · auto-refresh ' +
      (interval / 1000) + 's · ~ = estimate';
    scheduleNext(interval);
  }).catch(function(e){
    document.getElementById('meta').textContent = 'Error fetching status: ' + e;
    scheduleNext(IDLE_INTERVAL);
  });
}
refresh();
</script>
</body>
</html>"""


def open_browser():
    webbrowser.open('http://127.0.0.1:%d/' % PORT)


if __name__ == '__main__':
    # Open the browser shortly after the server starts. The reloader would
    # otherwise trigger this twice, so it is disabled.
    threading.Timer(1.0, open_browser).start()
    app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)
