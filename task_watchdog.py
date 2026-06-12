from __future__ import absolute_import
from __future__ import print_function
from courtutils.databases.postgres import PostgresDatabase
from time import sleep
import datetime

STALE_THRESHOLD_SECONDS = 120  # reset tasks with no heartbeat for 2+ minutes
CHECK_INTERVAL_SECONDS = 60    # check every minute

print('Watchdog running')

while True:
    for court_type in ['circuit', 'district']:
        try:
            db = PostgresDatabase(court_type)
            reset_count = db.reset_stale_tasks(STALE_THRESHOLD_SECONDS)
            if reset_count > 0:
                print('[%s] [%s] Reset %d stale task(s)' % (
                    datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    court_type,
                    reset_count
                ))
            db.disconnect()
        except Exception as e:
            print('Error checking %s tasks: %s' % (court_type, str(e)))
    sleep(CHECK_INTERVAL_SECONDS)
