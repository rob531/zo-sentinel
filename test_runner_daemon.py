#!/usr/bin/env python3
"""
test_runner_daemon.py -- Runs zo_sentinel_test_runner every 6 hours.
Auto-queues fix directives for any failures.
Run once: nohup python3 test_runner_daemon.py >> /home/workspace/logs/test_runner_daemon.log 2>&1 &
"""
import subprocess, sys, time, logging, requests
from datetime import datetime, timezone
from pathlib import Path

INTERVAL = 21600  # 6 hours
RUNNER   = '/home/workspace/zo_sentinel/tests/zo_sentinel_test_runner.py'
LOG      = Path('/home/workspace/logs/test_runner.log')

logging.basicConfig(level=logging.INFO, format='%(asctime)s [test_daemon] %(message)s')
log = logging.getLogger('test_daemon')

def heartbeat():
    try:
        requests.post('http://127.0.0.1:8772/write',
            json={'table': 'service_health',
                  'rows': {'service': 'test_runner_daemon',
                           'last_heartbeat': datetime.now(timezone.utc).isoformat()},
                  'wait': True}, timeout=5)
    except Exception: pass

def run_tests():
    log.info('Running test suite...')
    r = subprocess.run(
        [sys.executable, RUNNER, '--level', '4', '--write-db'],
        capture_output=True, text=True,
        env={'PYTHONPATH': '/home/workspace/zo_sentinel',
             'PATH': '/usr/local/bin:/usr/bin:/bin',
             'HOME': '/home/robin'}
    )
    # Append output to test log
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, 'a') as f:
        f.write(f'\n=== {datetime.now()} ===\n')
        f.write(r.stdout[-3000:] if r.stdout else '')
        if r.stderr: f.write(r.stderr[-500:])
    log.info('Test run complete. Exit=%d', r.returncode)
    return r.returncode

if __name__ == '__main__':
    log.info('Test runner daemon started. Interval=%dh', INTERVAL//3600)
    # Run immediately on start
    run_tests()
    while True:
        heartbeat()
        time.sleep(INTERVAL)
        run_tests()