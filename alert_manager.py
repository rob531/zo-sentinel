#!/usr/bin/env python3
"""
alert_manager.py -- ZO-SENTINEL Alert Manager Daemon.
Polls mcp_threat_associations and audit_log for critical security events
and routes notifications to configured endpoints.
"""
import os
import sys
import signal
import time
import hashlib
import logging
from datetime import datetime, timezone
from collections import defaultdict

import requests

SERVICE_NAME = 'alert_manager'
PORT = None
PID_FILE = '/tmp/alert_manager.pid'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_URL = 'http://127.0.0.1:8772/query'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
NOTIFY_TIMEOUT = 30
POLL_SECS = 300
HEARTBEAT_INTERVAL = 60

_log = None


def _setup_log():
    global _log
    if _log is not None:
        return _log
    log_dir = '/home/workspace/logs'
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'{SERVICE_NAME}.log')
    _log = logging.getLogger(SERVICE_NAME)
    _log.setLevel(logging.INFO)
    for h in _log.handlers[:]:
        _log.removeHandler(h)
    fh = logging.FileHandler(log_path)
    fmt = logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    fh.setFormatter(fmt)
    _log.addHandler(fh)
    return _log


log = _setup_log()


def ws_query(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(QUERY_URL, json=payload, timeout=NOTIFY_TIMEOUT)
    resp.raise_for_status()
    result = resp.json()
    return result.get('rows', [])


def ws_write(table, rows):
    url = f'{WRITE_SERVICE_URL}/write'
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(url, json=payload, timeout=NOTIFY_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=NOTIFY_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat():
    try:
        ws_write('service_health', [{
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        }])
    except Exception as e:
        log.warning('Heartbeat failed: %s', e)


def check_single_instance():
    pid_dir = os.path.dirname(PID_FILE) or '/tmp'
    os.makedirs(pid_dir, exist_ok=True)
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
        except (ValueError, IOError):
            old_pid = None
        if old_pid and _is_running(old_pid):
            log.error('Another instance already running (PID %s). Exiting.', old_pid)
            sys.exit(1)
        log.info('Stale PID file removed (PID %s)', old_pid)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def _is_running(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def remove_pid_file():
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    sig = signal.Signals(signum).name
    log.info('Received %s, shutting down gracefully.', sig)
    remove_pid_file()
    sys.exit(0)


_sent_history = {}


def _dedup_key(event_type, server_id, severity, detail):
    raw = f'{event_type}:{server_id}:{severity}:{detail}'
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _notify(title, body, severity='info'):
    notifier_url = os.environ.get('ALERT_NOTIFY_URL')
    if not notifier_url:
        log.debug('ALERT_NOTIFY_URL not set; alert logged only.')
        return
    try:
        resp = requests.post(
            notifier_url,
            json={'title': title, 'body': body, 'severity': severity},
            timeout=NOTIFY_TIMEOUT
        )
        resp.raise_for_status()
        log.info('Notification sent: %s', title)
    except requests.RequestException as e:
        log.warning('Notification failed for "%s": %s', title, e)


def _log_alert_to_audit(event_type, server_id, severity, evidence):
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        'event_id': hashlib.sha256(f'{ts}{event_type}{server_id}'.encode()).hexdigest()[:32],
        'event_type': event_type,
        'actor': 'alert_manager',
        'action': 'alert_routed',
        'target_server_id': server_id or '',
        'details_json': evidence if isinstance(evidence, str) else str(evidence),
        'outcome': 'delivered',
        'timestamp': ts,
        'immutable': False
    }
    try:
        ws_write('audit_log', [row])
    except Exception as e:
        log.warning('Failed to write alert audit entry: %s', e)


def get_recent_threat_associations(since_minutes=60):
    sql = """
        SELECT server_id, threat_type, severity, evidence, reported_at
        FROM mcp_threat_associations
        WHERE reported_at >= NOW() - INTERVAL '60 minutes'
        ORDER BY reported_at DESC
    """
    return ws_query(sql)


def get_critical_audit_events(since_minutes=60):
    sql = """
        SELECT event_id, event_type, actor, action, target_server_id, details_json, outcome, timestamp
        FROM audit_log
        WHERE timestamp >= NOW() - INTERVAL '60 minutes'
          AND outcome IN ('failure', 'denied', 'blocked', 'rejected')
        ORDER BY timestamp DESC
    """
    return ws_query(sql)


def get_high_risk_servers(threshold=80):
    sql = """
        SELECT server_id, risk_tier, risk_rank, computed_at
        FROM mcp_risk_register
        WHERE risk_tier IN ('CRITICAL', 'HIGH')
        ORDER BY risk_rank DESC
        LIMIT 50
    """
    return ws_query(sql)


def _dispatch_threat_alert(row):
    key = _dedup_key('threat_association', row.get('server_id', ''),
                     row.get('severity', ''), row.get('evidence', ''))
    if key in _sent_history:
        return
    _sent_history[key] = True
    title = f"[{row.get('severity', 'UNKNOWN')}] Threat: {row.get('threat_type', 'unknown')}"
    body = (f"Server: {row.get('server_id', 'N/A')}\n"
            f"Threat: {row.get('threat_type', 'N/A')}\n"
            f"Severity: {row.get('severity', 'N/A')}\n"
            f"Evidence: {row.get('evidence', 'N/A')}\n"
            f"Reported: {row.get('reported_at', 'N/A')}")
    _notify(title, body, severity=row.get('severity', 'info'))
    _log_alert_to_audit('threat_association', row.get('server_id', ''),
                         row.get('severity', ''), row.get('evidence', ''))


def _dispatch_audit_alert(row):
    key = _dedup_key('audit_failure', row.get('target_server_id', ''),
                     row.get('outcome', ''), row.get('details_json', ''))
    if key in _sent_history:
        return
    _sent_history[key] = True
    title = f"[AUDIT] {row.get('event_type', 'event')} -> {row.get('outcome', 'unknown')}"
    body = (f"Event: {row.get('event_type', 'N/A')}\n"
            f"Actor: {row.get('actor', 'N/A')}\n"
            f"Action: {row.get('action', 'N/A')}\n"
            f"Target: {row.get('target_server_id', 'N/A')}\n"
            f"Outcome: {row.get('outcome', 'N/A')}\n"
            f"Details: {row.get('details_json', 'N/A')}\n"
            f"Time: {row.get('timestamp', 'N/A')}")
    _notify(title, body, severity='warning')
    _log_alert_to_audit(row.get('event_type', ''), row.get('target_server_id', ''),
                         row.get('outcome', ''), row.get('details_json', ''))


def _dispatch_risk_alert(row):
    key = _dedup_key('high_risk', row.get('server_id', ''),
                     row.get('risk_tier', ''), row.get('risk_rank', 0))
    if key in _sent_history:
        return
    _sent_history[key] = True
    title = f"[{row.get('risk_tier', 'UNKNOWN')}] High-Risk Server: {row.get('server_id', 'N/A')}"
    body = (f"Server: {row.get('server_id', 'N/A')}\n"
            f"Risk Tier: {row.get('risk_tier', 'N/A')}\n"
            f"Risk Rank: {row.get('risk_rank', 'N/A')}\n"
            f"Computed: {row.get('computed_at', 'N/A')}")
    _notify(title, body, severity='error')
    _log_alert_to_audit('high_risk_alert', row.get('server_id', ''),
                         row.get('risk_tier', ''), f"rank={row.get('risk_rank', 0)}")


def cycle():
    log.info('--- Alert scan cycle ---')
    dispatched = 0

    threats = get_recent_threat_associations()
    for row in threats:
        _dispatch_threat_alert(row)
        dispatched += 1

    audit_rows = get_critical_audit_events()
    for row in audit_rows:
        _dispatch_audit_alert(row)
        dispatched += 1

    high_risk = get_high_risk_servers()
    for row in high_risk:
        _dispatch_risk_alert(row)
        dispatched += 1

    if dispatched:
        log.info('Dispatched %d alert(s).', dispatched)
    else:
        log.debug('No new critical events detected.')

    oldest_keys = sorted(_sent_history.keys())[:max(0, len(_sent_history) - 1000)]
    for k in oldest_keys:
        del _sent_history[k]


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info('%s started (PID %s). Poll interval: %ds', SERVICE_NAME, os.getpid(), POLL_SECS)
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': datetime.now(timezone.utc).isoformat()
    }])
    while True:
        try:
            cycle()
        except Exception as e:
            log.exception('Cycle error: %s', e)
        try:
            send_heartbeat()
        except Exception as e:
            log.warning('Heartbeat error: %s', e)
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()