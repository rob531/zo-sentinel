import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename='/home/workspace/logs/server_scoring_timeline.log'
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'server_scoring_timeline'
PORT = 0
PID_FILE = '/tmp/server_scoring_timeline.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
POLL_SECS = 300
HEARTBEAT_INTERVAL = 300


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query write_service for SELECT statements."""
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to DuckDB via write_service."""
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed for table {table}: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        resp = requests.post(EXECUTE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> bool:
    """Ensure only one instance runs at a time."""
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        if old_pid != pid:
            try:
                os.kill(old_pid, 0)
                log.error(f"Another instance running with PID {old_pid}")
                return False
            except OSError:
                log.warning(f"Stale PID file, overwriting")
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    return True


def remove_pid_file() -> None:
    """Remove PID file on shutdown."""
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame) -> None:
    """Handle shutdown signals gracefully."""
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    raise SystemExit(0)


def ensure_timeline_table() -> None:
    """Create server_scoring_timeline table if not exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS server_scoring_timeline (
        server_id VARCHAR,
        event_type VARCHAR,
        event_detail VARCHAR,
        event_at TIMESTAMPTZ,
        source VARCHAR,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """
    ws_execute(sql)
    sql_idx = """
    CREATE SEQUENCE IF NOT EXISTS server_scoring_timeline_seq
    """
    ws_execute(sql_idx)
    sql_pk = """
    ALTER TABLE server_scoring_timeline 
    ADD COLUMN IF NOT EXISTS id BIGINT DEFAULT nextval('server_scoring_timeline_seq')
    """
    ws_execute(sql_pk)
    sql_idx2 = """
    CREATE INDEX IF NOT EXISTS idx_timeline_server_id 
    ON server_scoring_timeline(server_id)
    """
    ws_execute(sql_idx2)


def ensure_timeline_events_table() -> None:
    """Create timeline_events table for tracking scoring milestones."""
    sql = """
    CREATE TABLE IF NOT EXISTS timeline_events (
        server_id VARCHAR,
        milestone VARCHAR,
        milestone_at TIMESTAMPTZ,
        metadata VARCHAR,
        created_at TIMESTAMPTZ DEFAULT NOW()
    )
    """
    ws_execute(sql)


def get_servers_needing_timeline() -> List[Dict[str, Any]]:
    """Find servers that have scoring data but no timeline entries."""
    sql = """
    SELECT DISTINCT sr.server_id, sr.name, sr.verdict, sr.trust_score,
           sr.first_seen, sr.last_scanned, sr.last_assessed
    FROM mcp_server_registry sr
    WHERE NOT EXISTS (
        SELECT 1 FROM server_scoring_timeline t 
        WHERE t.server_id = sr.server_id
    )
    AND sr.verdict != 'unknown'
    LIMIT 500
    """
    return ws_query(sql)


def get_servers_with_partial_timeline() -> List[Dict[str, Any]]:
    """Find servers that have some timeline but missing milestones."""
    sql = """
    SELECT sr.server_id, sr.name, sr.verdict, sr.trust_score,
           sr.first_seen, sr.last_scanned, sr.last_assessed,
           (SELECT COUNT(*) FROM server_scoring_timeline t 
            WHERE t.server_id = sr.server_id) as event_count
    FROM mcp_server_registry sr
    WHERE EXISTS (
        SELECT 1 FROM server_scoring_timeline t 
        WHERE t.server_id = sr.server_id
    )
    AND NOT EXISTS (
        SELECT 1 FROM timeline_events te 
        WHERE te.server_id = sr.server_id
    )
    LIMIT 200
    """
    return ws_query(sql)


def get_verdict_change_history(server_id: str) -> List[Dict[str, Any]]:
    """Get all verdict changes for a server from audit log."""
    sql = """
    SELECT server_id, event_type, actor, detail, created_at
    FROM audit_log
    WHERE server_id = %s
    AND event_type IN ('verdict_change', 'verdict_override', 'assessment_complete')
    ORDER BY created_at ASC
    """
    return ws_query(sql)


def get_signal_score_history(server_id: str) -> List[Dict[str, Any]]:
    """Get signal score history for a server."""
    sql = """
    SELECT server_id, signal_name, score, scored_at
    FROM mcp_signal_scores
    WHERE server_id = %s
    ORDER BY scored_at ASC
    """
    return ws_query(sql)


def compute_time_to_first_score(server_id: str, first_seen: Optional[str]) -> Optional[float]:
    """Compute hours between first_seen and first signal score."""
    if not first_seen:
        return None
    sql = """
    SELECT MIN(scored_at) as first_score_at
    FROM mcp_signal_scores
    WHERE server_id = %s
    """
    rows = ws_query(sql)
    if rows and rows[0].get('first_score_at'):
        first_score = rows[0]['first_score_at']
        try:
            fs_dt = datetime.fromisoformat(first_seen.replace('Z', '+00:00'))
            score_dt = datetime.fromisoformat(first_score.replace('Z', '+00:00'))
            delta = score_dt - fs_dt
            return delta.total_seconds() / 3600.0
        except Exception:
            return None
    return None


def compute_time_to_verdict(server_id: str, first_seen: Optional[str], verdict: str) -> Optional[float]:
    """Compute hours between first_seen and verdict assignment."""
    if not first_seen or verdict == 'unknown':
        return None
    sql = """
    SELECT MIN(created_at) as verdict_at
    FROM audit_log
    WHERE server_id = %s
    AND event_type IN ('verdict_change', 'verdict_override', 'assessment_complete')
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows and rows[0].get('verdict_at'):
        verdict_at = rows[0]['verdict_at']
        try:
            fs_dt = datetime.fromisoformat(first_seen.replace('Z', '+00:00'))
            v_dt = datetime.fromisoformat(verdict_at.replace('Z', '+00:00'))
            delta = v_dt - fs_dt
            return delta.total_seconds() / 3600.0
        except Exception:
            return None
    return None


def build_timeline_for_server(server_id: str, first_seen: Optional[str]) -> List[Dict[str, Any]]:
    """Build timeline events for a server based on available data."""
    events = []
    now = utc_now_iso()
    source = 'scoring_timeline_daemon'
    
    if first_seen:
        events.append({
            'server_id': server_id,
            'event_type': 'server_discovered',
            'event_detail': 'Server added to registry',
            'event_at': first_seen,
            'source': source
        })
    
    verdict_changes = get_verdict_change_history(server_id)
    for vc in verdict_changes:
        events.append({
            'server_id': server_id,
            'event_type': vc.get('event_type', 'verdict_change'),
            'event_detail': vc.get('detail', ''),
            'event_at': vc.get('created_at', now),
            'source': source
        })
    
    signal_history = get_signal_score_history(server_id)
    if signal_history:
        first_signal = signal_history[0]
        events.append({
            'server_id': server_id,
            'event_type': 'first_signal_scored',
            'event_detail': f"Score: {first_signal.get('score', 'N/A')}",
            'event_at': first_signal.get('scored_at', now),
            'source': source
        })
        
        latest_signal = signal_history[-1]
        if latest_signal != first_signal:
            events.append({
                'server_id': server_id,
                'event_type': 'latest_signal_update',
                'event_detail': f"Score: {latest_signal.get('score', 'N/A')}",
                'event_at': latest_signal.get('scored_at', now),
                'source': source
            })
    
    attestation_sql = """
    SELECT attested_at, attestation_level
    FROM mcp_attestations
    WHERE server_id = %s
    ORDER BY attested_at ASC
    LIMIT 1
    """
    attestations = ws_query(attestation_sql)
    if attestations:
        att = attestations[0]
        events.append({
            'server_id': server_id,
            'event_type': 'first_attestation',
            'event_detail': f"Level: {att.get('attestation_level', 'unknown')}",
            'event_at': att.get('attested_at', now),
            'source': source
        })
    
    return events


def build_milestones_for_server(server_id: str, first_seen: Optional[str], 
                                last_scanned: Optional[str], last_assessed: Optional[str],
                                verdict: str) -> List[Dict[str, Any]]:
    """Build milestone events for timeline summary."""
    milestones = []
    now = utc_now_iso()
    
    if first_seen:
        milestones.append({
            'server_id': server_id,
            'milestone': 'discovered',
            'milestone_at': first_seen,
            'metadata': 'Server added to registry'
        })
    
    if last_scanned:
        milestones.append({
            'server_id': server_id,
            'milestone': 'first_scan_complete',
            'milestone_at': last_scanned,
            'metadata': 'Initial scan performed'
        })
    
    if last_assessed:
        milestones.append({
            'server_id': server_id,
            'milestone': 'first_assessment_complete',
            'milestone_at': last_assessed,
            'metadata': f"Initial assessment complete, verdict: {verdict}"
        })
    
    time_to_score = compute_time_to_first_score(server_id, first_seen)
    if time_to_score is not None:
        milestones.append({
            'server_id': server_id,
            'milestone': 'first_score_achieved',
            'milestone_at': now,
            'metadata': f"Time to first score: {time_to_score:.2f} hours"
        })
    
    time_to_verdict = compute_time_to_verdict(server_id, first_seen, verdict)
    if time_to_verdict is not None:
        milestones.append({
            'server_id': server_id,
            'milestone': 'verdict_achieved',
            'milestone_at': now,
            'metadata': f"Time to verdict: {time_to_verdict:.2f} hours"
        })
    
    return milestones


def compute_timeline_metrics() -> Dict[str, Any]:
    """Compute aggregate timeline metrics for dashboard."""
    metrics = {}
    
    sql_avg_time_to_score = """
    SELECT AVG(EXTRACT(EPOCH FROM (first_score_at - first_seen)) / 3600) as avg_hours_to_score
    FROM (
        SELECT sr.first_seen,
               (SELECT MIN(scored_at) FROM mcp_signal_scores ss 
                WHERE ss.server_id = sr.server_id) as first_score_at
        FROM mcp_server_registry sr
        WHERE sr.first_seen IS NOT NULL
        AND EXISTS (SELECT 1 FROM mcp_signal_scores ss 
                    WHERE ss.server_id = sr.server_id)
        LIMIT 1000
    ) sub
    """
    rows = ws_query(sql_avg_time_to_score)
    if rows:
        metrics['avg_hours_to_first_score'] = rows[0].get('avg_hours_to_score')
    
    sql_verdict_distribution = """
    SELECT verdict, COUNT(*) as count
    FROM mcp_server_registry
    GROUP BY verdict
    ORDER BY count DESC
    """
    metrics['verdict_distribution'] = ws_query(sql_verdict_distribution)
    
    sql_timeline_coverage = """
    SELECT 
        (SELECT COUNT(DISTINCT server_id) FROM mcp_server_registry) as total_servers,
        (SELECT COUNT(DISTINCT server_id) FROM server_scoring_timeline) as servers_with_timeline
    """
    rows = ws_query(sql_timeline_coverage)
    if rows:
        total = rows[0].get('total_servers', 0)
        with_timeline = rows[0].get('servers_with_timeline', 0)
        metrics['timeline_coverage_pct'] = (with_timeline / total * 100) if total > 0 else 0
    
    sql_event_type_counts = """
    SELECT event_type, COUNT(*) as count
    FROM server_scoring_timeline
    GROUP BY event_type
    ORDER BY count DESC
    """
    metrics['event_type_counts'] = ws_query(sql_event_type_counts)
    
    return metrics


def cycle() -> int:
    """Process one cycle of timeline updates. Returns count of servers processed."""
    ensure_timeline_table()
    ensure_timeline_events_table()
    
    processed = 0
    
    servers_needing = get_servers_needing_timeline()
    log.info(f"Found {len(servers_needing)} servers needing timeline")
    
    for server in servers_needing:
        server_id = server.get('server_id')
        first_seen = server.get('first_seen')
        
        if not server_id:
            continue
        
        events = build_timeline_for_server(server_id, first_seen)
        if events:
            ws_write('server_scoring_timeline', events)
        
        milestones = build_milestones_for_server(
            server_id,
            first_seen,
            server.get('last_scanned'),
            server.get('last_assessed'),
            server.get('verdict', 'unknown')
        )
        if milestones:
            ws_write('timeline_events', milestones)
        
        processed += 1
    
    servers_partial = get_servers_with_partial_timeline()
    log.info(f"Found {len(servers_partial)} servers with partial timeline")
    
    for server in servers_partial:
        server_id = server.get('server_id')
        first_seen = server.get('first_seen')
        
        if not server_id:
            continue
        
        milestones = build_milestones_for_server(
            server_id,
            first_seen,
            server.get('last_scanned'),
            server.get('last_assessed'),
            server.get('verdict', 'unknown')
        )
        if milestones:
            ws_write('timeline_events', milestones)
        
        processed += 1
    
    metrics = compute_timeline_metrics()
    log.info(f"Timeline metrics: coverage={metrics.get('timeline_coverage_pct', 0):.1f}%, "
             f"avg_hours_to_score={metrics.get('avg_hours_to_first_score', 0):.2f}")
    
    return processed


def send_heartbeat() -> None:
    """Send heartbeat to service_health table."""
    now = utc_now_iso()
    metrics = compute_timeline_metrics()
    row = {
        'service': SERVICE_NAME,
        'status': 'running',
        'last_heartbeat': now,
        'meta': f"coverage={metrics.get('timeline_coverage_pct', 0):.1f}%"
    }
    ws_write('service_health', [row])


def run() -> None:
    """Main run loop for scoring timeline daemon."""
    import signal
    
    if not check_single_instance():
        log.error("Failed to acquire lock, exiting")
        return
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"Starting {SERVICE_NAME} daemon")
    
    while True:
        try:
            count = cycle()
            send_heartbeat()
            log.info(f"Cycle complete, processed {count} servers")
        except Exception as e:
            log.error(f"Error in cycle: {e}")
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()