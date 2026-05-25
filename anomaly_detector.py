#!/usr/bin/env python3
"""
anomaly_detector.py -- ZO-SENTINEL anomaly detection daemon.
Detects statistical anomalies, score clustering anomalies, and temporal anomalies
in MCP server trust signals. Runs every 43200s.
"""
import logging
import time
import os
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple
import requests
import fcntl
import signal

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

SERVICE_NAME = "anomaly_detector"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8773/query"
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 43200
STDDEV_THRESHOLD = 2.0
CLUSTERING_THRESHOLD = 0.90
TEMPORAL_CHANGE_THRESHOLD = 30.0
PID_FILE = "/tmp/anomaly_detector.pid"
REPORT_FILE = "ANOMALY_REPORT.md"


def get_write_url():
    return WRITE_SERVICE_URL


def get_execute_url():
    return EXECUTE_URL


def get_query_url():
    return QUERY_URL


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute SQL query via write_service execute endpoint."""
    try:
        response = requests.post(get_execute_url(), json={'sql': sql}, timeout=60)
        if response.status_code == 200:
            result = response.json()
            if 'result' in result and isinstance(result['result'], list):
                return result['result']
            elif 'rows' in result:
                return result['rows']
            return result.get('data', [])
        else:
            log.error(f"Query failed: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        log.error(f"Query error: {e}")
        return []


def ws_write(table: str, rows: Dict[str, Any], wait: bool = True) -> bool:
    """Write data to write_service."""
    try:
        payload = {'table': table, 'rows': rows, 'wait': wait}
        response = requests.post(get_write_url(), json=payload, timeout=30)
        if response.status_code == 200:
            return True
        else:
            log.error(f"Write failed: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        log.error(f"Write error: {e}")
        return False


def send_heartbeat():
    """Send heartbeat to service_health."""
    try:
        requests.post(get_write_url(), json={
            'table': 'service_health',
            'rows': {
                'service': SERVICE_NAME,
                'last_heartbeat': datetime.now(timezone.utc).isoformat()
            },
            'wait': True
        }, timeout=5)
        log.debug("Heartbeat sent")
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def check_single_instance() -> bool:
    """Ensure only one instance of this daemon runs."""
    pid_file = PID_FILE
    lock_file = open(pid_file, 'w')
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.write(str(os.getpid()))
        lock_file.flush()
        return True
    except IOError:
        log.error(f"Another instance of {SERVICE_NAME} is already running")
        return False


def remove_pid_file():
    """Remove PID file on shutdown."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.warning(f"Failed to remove PID file: {e}")


def compute_mean(values: List[float]) -> float:
    """Compute arithmetic mean."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def compute_stddev(values: List[float]) -> Tuple[float, float, float]:
    """Compute standard deviation. Returns (stddev, min, max)."""
    if len(values) < 2:
        return 0.0, values[0] if values else 0.0, values[0] if values else 0.0
    mean = compute_mean(values)
    variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    stddev = math.sqrt(variance)
    return stddev, min(values), max(values)


def get_all_signal_scores() -> Dict[str, List[float]]:
    """Fetch all signal scores from mcp_signal_scores table."""
    sql = """
    SELECT server_id, signal_name, score
    FROM mcp_signal_scores
    WHERE score IS NOT NULL
    ORDER BY server_id, signal_name
    """
    results = ws_query(sql)
    
    signal_scores: Dict[str, List[float]] = {}
    for row in results:
        signal_name = row.get('signal_name', '')
        score = row.get('score')
        if score is not None and signal_name:
            if signal_name not in signal_scores:
                signal_scores[signal_name] = []
            signal_scores[signal_name].append(float(score))
    
    return signal_scores


def detect_statistical_anomalies(signal_scores: Dict[str, List[float]]) -> List[Dict[str, Any]]:
    """Detect servers where any signal is >2 stddev from mean."""
    anomalies = []
    server_scores = {}
    
    sql = """
    SELECT server_id, signal_name, score, evidence
    FROM mcp_signal_scores
    WHERE score IS NOT NULL
    ORDER BY server_id, signal_name
    """
    results = ws_query(sql)
    
    for row in results:
        server_id = row.get('server_id', '')
        signal_name = row.get('signal_name', '')
        score = row.get('score')
        evidence = row.get('evidence', '')
        
        if server_id not in server_scores:
            server_scores[server_id] = []
        if score is not None:
            server_scores[server_id].append({
                'signal_name': signal_name,
                'score': float(score),
                'evidence': evidence
            })
    
    for signal_name, values in signal_scores.items():
        if len(values) < 3:
            continue
            
        mean = compute_mean(values)
        stddev, min_val, max_val = compute_stddev(values)
        
        if stddev == 0:
            continue
        
        for server_id, scores in server_scores.items():
            for entry in scores:
                if entry['signal_name'] == signal_name:
                    z_score = abs(entry['score'] - mean) / stddev
                    if z_score > STDDEV_THRESHOLD:
                        anomalies.append({
                            'server_id': server_id,
                            'anomaly_type': 'statistical_anomaly',
                            'signal_name': signal_name,
                            'score': entry['score'],
                            'mean': mean,
                            'stddev': stddev,
                            'z_score': z_score,
                            'evidence': entry['evidence'] or f'Score {entry["score"]} is {z_score:.2f} stddev from mean {mean:.2f}'
                        })
    
    return anomalies


def detect_score_clustering_anomaly() -> Optional[Dict[str, Any]]:
    """Detect if 90% of servers have near-identical trust scores."""
    sql = """
    SELECT server_id, trust_score, COUNT(*) as cnt
    FROM mcp_server_registry
    WHERE trust_score IS NOT NULL
    GROUP BY server_id, trust_score
    ORDER BY trust_score
    """
    results = ws_query(sql)
    
    if not results:
        return None
    
    server_scores = {}
    for row in results:
        server_id = row.get('server_id', '')
        trust_score = row.get('trust_score')
        if trust_score is not None and server_id:
            server_scores[server_id] = float(trust_score)
    
    if len(server_scores) < 10:
        return None
    
    score_values = list(server_scores.values())
    mean = compute_mean(score_values)
    stddev, min_val, max_val = compute_stddev(score_values)
    
    if stddev < 0.01:
        return {
            'anomaly_type': 'score_clustering_anomaly',
            'severity': 'HIGH',
            'evidence': f'All servers have near-identical trust scores (stddev={stddev:.4f}). Scoring is not discriminating.',
            'server_count': len(server_scores),
            'mean_score': mean,
            'stddev': stddev
        }
    
    score_groups = {}
    for server_id, score in server_scores.items():
        bucket = round(score, 1)
        if bucket not in score_groups:
            score_groups[bucket] = []
        score_groups[bucket].append(server_id)
    
    max_group_size = max(len(group) for group in score_groups.values())
    max_group_ratio = max_group_size / len(server_scores)
    
    if max_group_ratio >= CLUSTERING_THRESHOLD:
        dominant_score = max(score_groups.keys(), key=lambda k: len(score_groups[k]))
        return {
            'anomaly_type': 'score_clustering_anomaly',
            'severity': 'MEDIUM',
            'evidence': f'{max_group_ratio*100:.1f}% of servers clustered at trust_score={dominant_score}. Scoring lacks discrimination.',
            'server_count': len(server_scores),
            'dominant_score': dominant_score,
            'cluster_ratio': max_group_ratio
        }
    
    return None


def get_previous_assessment(server_id: str) -> Optional[Dict[str, Any]]:
    """Get previous assessment for a server."""
    sql = f"""
    SELECT server_id, trust_score, last_assessed
    FROM mcp_server_registry
    WHERE server_id = '{server_id}'
    ORDER BY last_assessed DESC
    LIMIT 1
    """
    results = ws_query(sql)
    return results[0] if results else None


def detect_temporal_anomalies() -> List[Dict[str, Any]]:
    """Detect servers where score changed >30 points with no new threat intel."""
    anomalies = []
    
    sql = """
    SELECT server_id, trust_score, last_assessed
    FROM mcp_server_registry
    WHERE trust_score IS NOT NULL
    ORDER BY server_id, last_assessed DESC
    """
    results = ws_query(sql)
    
    if not results:
        return anomalies
    
    server_history = {}
    for row in results:
        server_id = row.get('server_id', '')
        trust_score = row.get('trust_score')
        last_assessed = row.get('last_assessed', '')
        
        if server_id not in server_history:
            server_history[server_id] = []
        
        if trust_score is not None:
            server_history[server_id].append({
                'score': float(trust_score),
                'assessed': last_assessed
            })
    
    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    
    for server_id, history in server_history.items():
        if len(history) < 2:
            continue
        
        history_sorted = sorted(history, key=lambda x: x['assessed'] or '', reverse=True)
        current = history_sorted[0]
        previous = history_sorted[1]
        
        score_change = abs(current['score'] - previous['score'])
        
        if score_change > TEMPORAL_CHANGE_THRESHOLD:
            threat_sql = f"""
            SELECT COUNT(*) as cnt
            FROM mcp_threat_associations
            WHERE server_id = '{server_id}'
            AND reported_at > '{cutoff_date}'
            """
            threat_results = ws_query(threat_sql)
            new_threats = threat_results[0].get('cnt', 0) if threat_results else 0
            
            if new_threats == 0:
                anomalies.append({
                    'server_id': server_id,
                    'anomaly_type': 'temporal_anomaly',
                    'current_score': current['score'],
                    'previous_score': previous['score'],
                    'score_change': score_change,
                    'assessed_at': current['assessed'],
                    'evidence': f'Score changed by {score_change:.1f} points with no new threat intel in 30 days'
                })
    
    return anomalies


def write_anomaly_to_table(anomaly: Dict[str, Any]) -> bool:
    """Write anomaly to mcp_threat_associations table."""
    evidence = anomaly.get('evidence', '')
    if len(evidence) > 500:
        evidence = evidence[:497] + '...'
    
    rows = {
        'server_id': anomaly.get('server_id', 'SYSTEM'),
        'threat_type': anomaly.get('anomaly_type', 'unknown'),
        'severity': anomaly.get('severity', 'MEDIUM'),
        'evidence': evidence,
        'reported_at': datetime.now(timezone.utc).isoformat()
    }
    
    return ws_write('mcp_threat_associations', rows)


def generate_anomaly_report(
    statistical_anomalies: List[Dict[str, Any]],
    clustering_anomaly: Optional[Dict[str, Any]],
    temporal_anomalies: List[Dict[str, Any]],
    stats: Dict[str, Any]
) -> str:
    """Generate ANOMALY_REPORT.md content."""
    lines = []
    lines.append("# ZO-SENTINEL Anomaly Detection Report")
    lines.append("")
    lines.append(f"**Generated:** {datetime.now(timezone.utc).isoformat()}")
    lines.append(f"**Service:** {SERVICE_NAME}")
    lines.append("")
    
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Statistical Anomalies: {len(statistical_anomalies)}")
    lines.append(f"- Clustering Anomaly: {'Detected' if clustering_anomaly else 'None'}")
    lines.append(f"- Temporal Anomalies: {len(temporal_anomalies)}")
    lines.append("")
    
    lines.append("## Population Statistics")
    lines.append("")
    lines.append("| Signal | Count | Mean | StdDev | Min | Max |")
    lines.append("|--------|-------|------|--------|-----|-----|")
    for signal, values in stats.get('signal_stats', {}).items():
        lines.append(f"| {signal} | {values['count']} | {values['mean']:.3f} | {values['stddev']:.3f} | {values['min']:.3f} | {values['max']:.3f} |")
    lines.append("")
    
    if clustering_anomaly:
        lines.append("## Clustering Anomaly")
        lines.append("")
        lines.append(f"**Type:** {clustering_anomaly.get('anomaly_type')}")
        lines.append(f"**Severity:** {clustering_anomaly.get('severity')}")
        lines.append(f"**Evidence:** {clustering_anomaly.get('evidence')}")
        lines.append("")
    
    if statistical_anomalies:
        lines.append("## Statistical Anomalies")
        lines.append("")
        lines.append("| Server ID | Signal | Score | Mean | Z-Score | Evidence |")
        lines.append("|-----------|--------|-------|------|---------|----------|")
        for a in statistical_anomalies[:50]:
            evidence = a.get('evidence', '')[:50] + ('...' if len(a.get('evidence', '')) > 50 else '')
            lines.append(f"| {a.get('server_id', '')[:30]} | {a.get('signal_name', '')} | {a.get('score', 0):.3f} | {a.get('mean', 0):.3f} | {a.get('z_score', 0):.2f} | {evidence} |")
        lines.append("")
        if len(statistical_anomalies) > 50:
            lines.append(f"*... and {len(statistical_anomalies) - 50} more anomalies*")
            lines.append("")
    
    if temporal_anomalies:
        lines.append("## Temporal Anomalies")
        lines.append("")
        lines.append("| Server ID | Current Score | Previous Score | Change | Assessed |")
        lines.append("|-----------|---------------|-----------------|--------|----------|")
        for a in temporal_anomalies[:50]:
            lines.append(f"| {a.get('server_id', '')[:30]} | {a.get('current_score', 0):.1f} | {a.get('previous_score', 0):.1f} | {a.get('score_change', 0):.1f} | {a.get('assessed_at', '')[:19]} |")
        lines.append("")
        if len(temporal_anomalies) > 50:
            lines.append(f"*... and {len(temporal_anomalies) - 50} more anomalies*")
            lines.append("")
    
    if not statistical_anomalies and not clustering_anomaly and not temporal_anomalies:
        lines.append("## Findings")
        lines.append("")
        lines.append("No anomalies detected in this cycle. All signals within normal statistical bounds.")
        lines.append("")
    
    lines.append("---")
    lines.append(f"*Report generated by {SERVICE_NAME}*")
    
    return "\n".join(lines)


def run_cycle() -> Dict[str, Any]:
    """Run one anomaly detection cycle."""
    log.info("Starting anomaly detection cycle")
    cycle_start = datetime.now(timezone.utc)
    
    signal_scores = get_all_signal_scores()
    
    signal_stats = {}
    for signal_name, values in signal_scores.items():
        mean = compute_mean(values)
        stddev, min_val, max_val = compute_stddev(values)
        signal_stats[signal_name] = {
            'count': len(values),
            'mean': mean,
            'stddev': stddev,
            'min': min_val,
            'max': max_val
        }
    
    log.info(f"Computed statistics for {len(signal_stats)} signals")
    
    statistical_anomalies = detect_statistical_anomalies(signal_scores)
    log.info(f"Found {len(statistical_anomalies)} statistical anomalies")
    
    clustering_anomaly = detect_score_clustering_anomaly()
    if clustering_anomaly:
        log.info(f"Detected clustering anomaly: {clustering_anomaly.get('evidence', '')[:100]}")
    
    temporal_anomalies = detect_temporal_anomalies()
    log.info(f"Found {len(temporal_anomalies)} temporal anomalies")
    
    for anomaly in statistical_anomalies:
        write_anomaly_to_table(anomaly)
    
    if clustering_anomaly:
        write_anomaly_to_table(clustering_anomaly)
    
    for anomaly in temporal_anomalies:
        write_anomaly_to_table(anomaly)
    
    stats = {
        'signal_stats': signal_stats,
        'total_anomalies': len(statistical_anomalies) + (1 if clustering_anomaly else 0) + len(temporal_anomalies)
    }
    
    report = generate_anomaly_report(
        statistical_anomalies,
        clustering_anomaly,
        temporal_anomalies,
        stats
    )
    
    try:
        with open(REPORT_FILE, 'w') as f:
            f.write(report)
        log.info(f"Anomaly report written to {REPORT_FILE}")
    except Exception as e:
        log.error(f"Failed to write report: {e}")
    
    cycle_end = datetime.now(timezone.utc)
    duration = (cycle_end - cycle_start).total_seconds()
    log.info(f"Anomaly detection cycle completed in {duration:.1f}s")
    
    return stats


def heartbeat_loop():
    """Send periodic heartbeats."""
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    """Main entry point for anomaly detector daemon."""
    log.info(f"Starting {SERVICE_NAME}")
    
    if not check_single_instance():
        log.error(f"Failed to acquire lock - another instance may be running")
        return
    
    signal.signal(signal.SIGINT, lambda s, f: remove_pid_file())
    signal.signal(signal.SIGTERM, lambda s, f: remove_pid_file())
    
    try:
        import threading
        heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        
        while True:
            try:
                run_cycle()
            except Exception as e:
                log.error(f"Cycle failed: {e}")
            
            log.info(f"Sleeping for {CYCLE_INTERVAL}s until next cycle")
            time.sleep(CYCLE_INTERVAL)
            
    except KeyboardInterrupt:
        log.info("Received interrupt signal")
    finally:
        remove_pid_file()
        log.info(f"{SERVICE_NAME} stopped")


if __name__ == "__main__":
    run()