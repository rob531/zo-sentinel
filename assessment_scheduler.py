#!/usr/bin/env python3
"""
assessment_scheduler.py -- ZO-SENTINEL assessment scheduling daemon.
Coordinates reassessment timing to avoid simultaneous daemon runs.
"""
import requests
import time
import os
import signal
import sys
import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

SERVICE_NAME = 'assessment_scheduler'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 900
MAX_CONCURRENT_ASSESSMENTS = 5
SCHEDULE_MD_PATH = '/home/workspace/zo_sentinel/SCHEDULE.md'
PID_FILE = '/var/run/zo/assessment_scheduler.pid'

_assessment_lock = threading.Lock()
_active_assessments = {}


def ws_query(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows, wait=True):
    url = f'{WRITE_SERVICE_URL}/write'
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat():
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")


def check_single_instance():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    def cleanup(signum, frame):
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        sys.exit(0)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)


def ensure_mesh_events_table():
    sql = """
    CREATE TABLE IF NOT EXISTS mesh_events (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        event_type VARCHAR NOT NULL,
        source_service VARCHAR NOT NULL,
        target_service VARCHAR,
        payload JSON,
        created_at TIMESTAMPTZ DEFAULT now(),
        processed BOOLEAN DEFAULT FALSE
    )
    """
    try:
        ws_query(sql)
    except Exception as e:
        print(f"Note: mesh_events table may already exist: {e}")


def get_running_assessment_count():
    sql = """
    SELECT COUNT(*) as count FROM mcp_signal_scores
    WHERE signal_name = 'assessment_in_progress'
    AND scored_at > NOW() - INTERVAL '10 minutes'
    """
    try:
        result = ws_query(sql)
        if result.get('rows'):
            return result['rows'][0].get('count', 0)
    except Exception as e:
        print(f"Error checking running assessments: {e}")
    return 0


def get_servers_with_expired_attestations():
    sql = """
    SELECT id, server_id, name, verdict, last_assessed,
           expiry_date, assessment_validity_days
    FROM (
        SELECT msr.id, msr.server_id, msr.name, msr.verdict, msr.last_assessed,
               msa.expiry_date,
               COALESCE(msa.assessment_validity_days, 30) as assessment_validity_days
        FROM mcp_server_registry msr
        LEFT JOIN mcp_signal_scores msa ON msr.server_id = msa.server_id
            AND msa.signal_name = 'attestation_expiry'
        WHERE msr.status = 'active'
    ) sub
    WHERE last_assessed IS NULL
       OR last_assessed < NOW() - INTERVAL '1 day'
       OR (expiry_date IS NOT NULL AND expiry_date < NOW())
       OR (last_assessed IS NOT NULL AND assessment_validity_days > 0
           AND last_assessed < NOW() - (assessment_validity_days || ' days')::INTERVAL)
    LIMIT ?
    """
    try:
        result = ws_query(sql, [MAX_CONCURRENT_ASSESSMENTS])
        return result.get('rows', [])
    except Exception as e:
        print(f"Error getting expired attestations: {e}")
        return []


def get_servers_with_new_threat_intel():
    sql = """
    SELECT DISTINCT msr.id, msr.server_id, msr.name, msr.verdict, msr.last_assessed,
           wa.published_at as threat_published_at, wa.topics
    FROM mcp_server_registry msr
    CROSS JOIN LATERAL (
        SELECT published_at, topics FROM world_articles
        WHERE published_at > COALESCE(msr.last_assessed, msr.first_seen)
        AND (topics::text LIKE '%mcp%' OR topics::text LIKE '%model context protocol%'
             OR topics::text LIKE '%cybersecurity%')
        ORDER BY published_at DESC
        LIMIT 3
    ) wa
    WHERE msr.status = 'active'
    AND msr.verdict IN ('APPROVED', 'APPROVED_WITH_CONDITIONS')
    LIMIT ?
    """
    try:
        result = ws_query(sql, [MAX_CONCURRENT_ASSESSMENTS])
        return result.get('rows', [])
    except Exception as e:
        print(f"Error getting new threat intel servers: {e}")
        return []


def get_servers_needing_insufficient_review():
    sql = """
    SELECT id, server_id, name, verdict, last_assessed,
           verdict_reasoning
    FROM mcp_server_registry
    WHERE status = 'active'
    AND verdict = 'INSUFFICIENT_ASSURANCE'
    AND last_assessed IS NOT NULL
    AND last_assessed < NOW() - INTERVAL '24 hours'
    ORDER BY last_assessed ASC
    LIMIT ?
    """
    try:
        result = ws_query(sql, [MAX_CONCURRENT_ASSESSMENTS])
        return result.get('rows', [])
    except Exception as e:
        print(f"Error getting insufficient review servers: {e}")
        return []


def get_servers_pending_review_too_long():
    sql = """
    SELECT id, server_id, name, verdict, last_assessed
    FROM mcp_server_registry
    WHERE status = 'active'
    AND verdict = 'PENDING_REVIEW'
    AND last_assessed < NOW() - INTERVAL '72 hours'
    ORDER BY last_assessed ASC
    LIMIT ?
    """
    try:
        result = ws_query(sql, [MAX_CONCURRENT_ASSESSMENTS])
        return result.get('rows', [])
    except Exception as e:
        print(f"Error getting pending review servers: {e}")
        return []


def get_stale_servers_never_assessed():
    sql = """
    SELECT id, server_id, name, verdict, last_assessed, first_seen
    FROM mcp_server_registry
    WHERE status = 'active'
    AND (last_assessed IS NULL OR last_assessed = first_seen)
    AND scan_count >= 2
    AND verdict IS NULL
    ORDER BY first_seen ASC
    LIMIT ?
    """
    try:
        result = ws_query(sql, [MAX_CONCURRENT_ASSESSMENTS])
        return result.get('rows', [])
    except Exception as e:
        print(f"Error getting never-assessed servers: {e}")
        return []


def emit_schedule_trigger(server, trigger_reason):
    payload = {
        'server_id': server.get('server_id'),
        'server_name': server.get('name'),
        'reason': trigger_reason,
        'triggered_at': datetime.now(timezone.utc).isoformat(),
        'priority': _get_priority_for_reason(trigger_reason)
    }
    sql = """
    INSERT INTO mesh_events (event_type, source_service, target_service, payload)
    VALUES (?, ?, ?, ?)
    """
    try:
        ws_query(sql, [
            'schedule_trigger',
            SERVICE_NAME,
            'signal_analyser',
            json.dumps(payload)
        ])
        ws_query(sql, [
            'schedule_trigger',
            SERVICE_NAME,
            'trust_synthesiser',
            json.dumps(payload)
        ])
        _mark_assessment_started(server.get('server_id'))
        return True
    except Exception as e:
        print(f"Error emitting schedule trigger: {e}")
        return False


def _get_priority_for_reason(reason):
    priorities = {
        'expired_attestation': 1,
        'new_threat_intel': 2,
        'insufficient_review': 3,
        'pending_review_stale': 4,
        'never_assessed': 5
    }
    return priorities.get(reason, 10)


def _mark_assessment_started(server_id):
    with _assessment_lock:
        _active_assessments[server_id] = datetime.now(timezone.utc)


def _cleanup_finished_assessments():
    with _assessment_lock:
        now = datetime.now(timezone.utc)
        stale_threshold = timedelta(minutes=15)
        finished = [
            sid for sid, start in _active_assessments.items()
            if now - start > stale_threshold
        ]
        for sid in finished:
            del _active_assessments[sid]


def get_priority_servers():
    all_priority = []
    seen_ids = set()
    def add_servers(servers, reason):
        for s in servers:
            if s.get('server_id') not in seen_ids:
                seen_ids.add(s.get('server_id'))
                s['_trigger_reason'] = reason
                all_priority.append(s)
    add_servers(get_servers_with_expired_attestations(), 'expired_attestation')
    add_servers(get_servers_with_new_threat_intel(), 'new_threat_intel')
    add_servers(get_servers_needing_insufficient_review(), 'insufficient_review')
    add_servers(get_servers_pending_review_too_long(), 'pending_review_stale')
    add_servers(get_stale_servers_never_assessed(), 'never_assessed')
    return all_priority


def update_schedule_md():
    now = datetime.now(timezone.utc)
    next_run = now + timedelta(seconds=POLL_INTERVAL)
    running = get_running_assessment_count()
    priority_servers = get_priority_servers()
    lines = [
        "# ZO-SENTINEL Assessment Schedule",
        "",
        f"Last Updated: {now.isoformat()}",
        "",
        "## Daemon Schedule",
        "",
        "| Service | Poll Interval | Next Run | Status |",
        "|---------|--------------|----------|--------|",
        f"| {SERVICE_NAME} | {POLL_INTERVAL}s | {next_run.isoformat()} | running |",
        "| signal_analyser | 300s | dynamic | event-driven |",
        "| trust_synthesiser | 600s | dynamic | event-driven |",
        "| threat_intel_ingestor | 900s | dynamic | scheduled |",
        "| risk_ranker | 1200s | dynamic | scheduled |",
        "| rug_pull_monitor | 600s | dynamic | scheduled |",
        "| policy_engine | 900s | dynamic | scheduled |",
        "",
        "## Concurrent Assessment Status",
        "",
        f"- Running: {running}",
        f"- Max Allowed: {MAX_CONCURRENT_ASSESSMENTS}",
        f"- Available Slots: {max(0, MAX_CONCURRENT_ASSESSMENTS - running)}",
        "",
        "## Priority Queue",
        "",
        "| Priority | Server ID | Name | Trigger Reason |",
        "|----------|-----------|------|----------------|",
    ]
    priority_labels = {
        'expired_attestation': 'Expired Attestation',
        'new_threat_intel': 'New Threat Intel',
        'insufficient_review': 'INSUFFICIENT 24h+',
        'pending_review_stale': 'PENDING_REVIEW 72h+',
        'never_assessed': 'Never Assessed'
    }
    for idx, server in enumerate(priority_servers[:10], 1):
        reason = priority_labels.get(server.get('_trigger_reason', 'unknown'), server.get('_trigger_reason', 'unknown'))
        lines.append(f"| {idx} | {server.get('server_id', 'N/A')} | {server.get('name', 'N/A')} | {reason} |")
    lines.extend(["", "## Max Concurrent Constraint", "", f"Maximum concurrent assessments enforced: {MAX_CONCURRENT_ASSESSMENTS}", ""])
    try:
        os.makedirs(os.path.dirname(SCHEDULE_MD_PATH), exist_ok=True)
        with open(SCHEDULE_MD_PATH, 'w') as f:
            f.write('\n'.join(lines))
    except Exception as e:
        print(f"Error updating SCHEDULE.md: {e}")


def cycle():
    send_heartbeat()
    _cleanup_finished_assessments()
    running = get_running_assessment_count()
    available = max(0, MAX_CONCURRENT_ASSESSMENTS - running)
    if available == 0:
        print(f"Max concurrent assessments ({MAX_CONCURRENT_ASSESSMENTS}) in progress, skipping scheduling")
        update_schedule_md()
        return
    priority_servers = get_priority_servers()
    scheduled = 0
    for server in priority_servers:
        if scheduled >= available:
            break
        if server.get('server_id') in _active_assessments:
            continue
        trigger_reason = server.get('_trigger_reason', 'unknown')
        if emit_schedule_trigger(server, trigger_reason):
            scheduled += 1
            print(f"Scheduled {server.get('name', server.get('server_id'))} due to: {trigger_reason}")
    update_schedule_md()


def run():
    print(f"Starting {SERVICE_NAME}...")
    check_single_instance()
    ensure_mesh_events_table()
    send_heartbeat()
    update_schedule_md()
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in cycle: {e}")
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    run()