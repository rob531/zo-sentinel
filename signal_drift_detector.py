#!/usr/bin/env python3
"""
signal_drift_detector.py -- ZO-SENTINEL signal drift detection daemon.
Every 43200s: compare current vs 7-day-old signal scores per server.
Detect drift >20 for signals, >15 for trust_score. Trigger re-attestation.
"""
import requests
import time
import os
import signal
import sys
from datetime import datetime, timezone, timedelta

SERVICE_NAME = 'signal_drift_detector'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 43200

DRIFT_THRESHOLD_SIGNAL = 20.0
DRIFT_THRESHOLD_TRUST = 15.0
HISTORICAL_WINDOW_DAYS = 7
MIN_ASSESSMENTS = 2

def ws_query(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json().get('data', [])

def ws_write(table, rows, wait=True):
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(WRITE_SERVICE_URL, json=payload)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat():
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': 'running'
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")

def check_single_instance():
    pid_file = f'/var/run/zo/{SERVICE_NAME}.pid'
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    def cleanup(signum, frame):
        if os.path.exists(pid_file):
            os.remove(pid_file)
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

def get_servers_with_history():
    sql = """
    SELECT server_id, COUNT(*) as assessment_count
    FROM mcp_signal_scores
    GROUP BY server_id
    HAVING COUNT(*) > ?
    """
    return ws_query(sql, [MIN_ASSESSMENTS])

def get_latest_scores(server_id):
    sql = """
    SELECT signal_name, score, scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    AND scored_at = (
        SELECT MAX(scored_at) FROM mcp_signal_scores
        WHERE server_id = ? AND signal_name = mcp_signal_scores.signal_name
    )
    """
    return ws_query(sql, [server_id, server_id])

def get_historical_scores(server_id, days_ago):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days_ago)
    sql = """
    SELECT signal_name, score, scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    AND scored_at <= ?
    AND scored_at = (
        SELECT MAX(scored_at) FROM mcp_signal_scores
        WHERE server_id = ?
        AND signal_name = mcp_signal_scores.signal_name
        AND scored_at <= ?
    )
    """
    return ws_query(sql, [server_id, cutoff.isoformat(), server_id, cutoff.isoformat()])

def get_trust_score_history(server_id):
    sql = """
    SELECT trust_score, last_assessed
    FROM mcp_servers
    WHERE server_id = ?
    ORDER BY last_assessed DESC
    LIMIT 10
    """
    return ws_query(sql, [server_id])

def compute_signal_deltas(current_scores, historical_scores):
    hist_dict = {s['signal_name']: s['score'] for s in historical_scores}
    deltas = []
    for curr in current_scores:
        signal_name = curr['signal_name']
        if signal_name in hist_dict:
            old_score = hist_dict[signal_name]
            delta = abs(curr['score'] - old_score)
            deltas.append({
                'signal': signal_name,
                'old_score': old_score,
                'new_score': curr['score'],
                'delta': delta
            })
    return deltas

def compute_trust_drift(server_id):
    history = get_trust_score_history(server_id)
    if len(history) < 2:
        return None
    latest = history[0]['trust_score']
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORICAL_WINDOW_DAYS)
    for h in history:
        if h['last_assessed']:
            if isinstance(h['last_assessed'], str):
                hdate = datetime.fromisoformat(h['last_assessed'].replace('Z', '+00:00'))
            else:
                hdate = h['last_assessed']
            if hdate <= cutoff:
                old = h['trust_score']
                return abs(latest - old) if (latest is not None and old is not None) else None
    return None

def record_signal_drift_event(server_id, signal, old_score, new_score, delta):
    try:
        ws_write('mesh_events', {
            'event_type': 'signal_drift_detected',
            'server_id': server_id,
            'payload': {
                'server_id': server_id,
                'signal': signal,
                'old_score': old_score,
                'new_score': new_score,
                'delta': delta,
                'detected_at': datetime.now(timezone.utc).isoformat()
            },
            'severity': 'WARNING',
            'detected_at': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        print(f"Failed to write signal drift event: {e}")

def trigger_re_attestation(server_id, trust_drift):
    try:
        directive = (
            f"Re-attestation required for server {server_id}: "
            f"trust_score drifted {trust_drift:.1f} points over "
            f"{HISTORICAL_WINDOW_DAYS} days. Previous trust_score "
            f"assessments show significant deviation from current baseline."
        )
        ws_write('mesh_memory', {
            'agent_id': 'zo_sentinel.directive',
            'memory_type': 'build_directive',
            'content': directive,
            'server_id': server_id,
            'trigger': 'trust_score_drift',
            'drift_magnitude': trust_drift,
            'created_at': datetime.now(timezone.utc).isoformat()
        })
        print(f"Triggered re-attestation for {server_id} (trust drift: {trust_drift:.1f})")
    except Exception as e:
        print(f"Failed to trigger re-attestation: {e}")

def cycle():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting signal drift detection cycle")
    servers = get_servers_with_history()
    print(f"Found {len(servers)} servers with sufficient assessment history")
    
    drift_events = 0
    re_attest_triggers = 0
    
    for server_row in servers:
        server_id = server_row['server_id']
        
        try:
            current_scores = get_latest_scores(server_id)
            if not current_scores:
                continue
                
            historical_scores = get_historical_scores(server_id, HISTORICAL_WINDOW_DAYS)
            if not historical_scores:
                continue
            
            deltas = compute_signal_deltas(current_scores, historical_scores)
            
            for d in deltas:
                if d['delta'] > DRIFT_THRESHOLD_SIGNAL:
                    record_signal_drift_event(
                        server_id,
                        d['signal'],
                        d['old_score'],
                        d['new_score'],
                        d['delta']
                    )
                    drift_events += 1
                    print(f"  Signal drift: {server_id}/{d['signal']} "
                          f"{d['old_score']:.1f} -> {d['new_score']:.1f} "
                          f"(delta: {d['delta']:.1f})")
            
            trust_drift = compute_trust_drift(server_id)
            if trust_drift and trust_drift > DRIFT_THRESHOLD_TRUST:
                trigger_re_attestation(server_id, trust_drift)
                re_attest_triggers += 1
                
        except Exception as e:
            print(f"Error processing server {server_id}: {e}")
    
    print(f"Cycle complete: {drift_events} signal drifts detected, "
          f"{re_attest_triggers} re-attestations triggered")

def run():
    check_single_instance()
    print(f"Starting {SERVICE_NAME}")
    send_heartbeat()
    
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in cycle: {e}")
        
        send_heartbeat()
        time.sleep(CYCLE_INTERVAL)

if __name__ == '__main__':
    run()