#!/usr/bin/env python3
"""
Signal Quality Dashboard Patch Generator
Generates real-time signal discrimination metrics for ZO-SENTINEL dashboard.
Patches signal_quality_weak_signal_audit.py with live distinct-value counts.
"""
import os
import sys
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import requests

# Constants
WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'signal_quality_dashboard_patch'
SERVICE_PORT = 8785
PID_FILE = f'/home/workspace/zo_sentinel/.{SERVICE_NAME}.pid'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

# Signal cardinality expectations (verified from live schema)
SIGNAL_CARDINALITIES = {
    'permission_scope': 4,
    'temporal_stability': 4,
    'tool_description_safety': 4,
    'domain_trust': 12,
    'community_signal': 34,
    'supply_chain_security': 8,
    'risk_score_distribution': 10,
    'attestation_coverage': 5,
}

# Configure logging - file handler only for daemon
logger = logging.getLogger(__name__)
file_handler = logging.FileHandler(LOG_FILE)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
logger.addHandler(file_handler)
logger.setLevel(logging.INFO)

def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        if old_pid and os.path.exists(f'/proc/{old_pid}'):
            logger.error(f"Already running as PID {old_pid}")
            return False
        os.remove(PID_FILE)
    return True

def remove_pid_file() -> None:
    """Remove PID file on exit."""
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def signal_handler(signum: int, frame) -> None:
    """Handle termination signals gracefully."""
    logger.info(f"Received signal {signum}, shutting down...")
    remove_pid_file()
    sys.exit(0)

def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to DuckDB via write_service HTTP."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed for {table}: {e}")
        return False

def ws_query(sql: str, params: Optional[List] = None) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service HTTP."""
    payload = {'table': '__query__', 'sql': sql}
    if params:
        payload['params'] = params
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        return result.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query failed: {sql[:100]}... Error: {e}")
        return []

def send_heartbeat(status: str = 'running', meta: Optional[Dict] = None) -> None:
    """Send heartbeat to service_health table."""
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'ts': ts,
        'meta': meta or {}
    }
    ws_write('service_health', [row])

def get_signal_distinct_counts() -> Dict[str, int]:
    """Query DuckDB for distinct value counts per signal."""
    counts = {}
    for signal_name in SIGNAL_CARDINALITIES.keys():
        sql = f"""
            SELECT COUNT(DISTINCT {signal_name}) as distinct_count
            FROM mcp_signal_scores
        """
        result = ws_query(sql)
        if result and len(result) > 0:
            counts[signal_name] = result[0].get('distinct_count', 0) or 0
        else:
            counts[signal_name] = 0
    return counts

def compute_discrimination_score(counts: Dict[str, int]) -> Dict[str, Any]:
    """Compute signal discrimination quality score."""
    total_expected = sum(SIGNAL_CARDINALITIES.values())
    total_actual = sum(counts.values())
    total_expected_max = sum(min(c, SIGNAL_CARDINALITIES[s]) for s, c in counts.items())
    
    if total_expected_max == 0:
        coverage_pct = 0.0
    else:
        coverage_pct = (total_actual / total_expected_max) * 100 if total_expected_max > 0 else 0
    
    individual_scores = {}
    for signal, cardinality in SIGNAL_CARDINALITIES.items():
        actual = counts.get(signal, 0)
        expected = cardinality
        if expected > 0:
            score = min(100.0, (actual / expected) * 100)
        else:
            score = 0.0
        individual_scores[signal] = {
            'actual': actual,
            'expected': expected,
            'score': round(score, 1)
        }
    
    return {
        'overall_coverage_pct': round(coverage_pct, 1),
        'total_distinct_combinations': total_actual,
        'signals': individual_scores
    }

def generate_html_patch(scores: Dict[str, Any]) -> str:
    """Generate HTML patch content for dashboard."""
    ts = datetime.now(timezone.utc).isoformat()
    
    signal_rows = []
    for signal, data in scores['signals'].items():
        status = 'healthy' if data['score'] >= 80 else 'warning' if data['score'] >= 50 else 'critical'
        signal_rows.append(f"""
            <tr class="signal-row {status}">
                <td class="signal-name">{signal}</td>
                <td class="signal-actual">{data['actual']}</td>
                <td class="signal-expected">{data['expected']}</td>
                <td class="signal-score">
                    <div class="score-bar">
                        <div class="score-fill {status}" style="width: {data['score']}%"></div>
                        <span class="score-text">{data['score']}%</span>
                    </div>
                </td>
                <td class="signal-status">{status.upper()}</td>
            </tr>
        """)
    
    html = f"""
<!-- SIGNAL QUALITY DASHBOARD PATCH - Auto-generated {ts} -->
<div id="signal-quality-patch" class="dashboard-section" data-updated="{ts}">
    <div class="patch-header">
        <h3>Signal Discrimination Metrics</h3>
        <div class="overall-status">
            <span class="label">Overall Coverage:</span>
            <span class="value {('healthy' if scores['overall_coverage_pct'] >= 70 else 'warning' if scores['overall_coverage_pct'] >= 40 else 'critical')}">
                {scores['overall_coverage_pct']}%
            </span>
            <span class="total-combos">({scores['total_distinct_combinations']} distinct signal combinations)</span>
        </div>
    </div>
    <table class="signal-metrics-table">
        <thead>
            <tr>
                <th>Signal</th>
                <th>Distinct Values</th>
                <th>Expected</th>
                <th>Discrimination Score</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            {''.join(signal_rows)}
        </tbody>
    </table>
    <div class="patch-footer">
        <span class="last-updated">Last updated: {ts}</span>
        <span class="cardinality-legend">Target cardinalities: permission_scope=4, temporal_stability=4, tool_description_safety=4, domain_trust=12, community_signal=34, supply_chain_security=8, risk_score_distribution=10, attestation_coverage=5</span>
    </div>
</div>

<style>
#signal-quality-patch {{
    background: #1a1a2e;
    border: 1px solid #4a4a6a;
    border-radius: 8px;
    padding: 16px;
    margin: 16px 0;
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
}}
#signal-quality-patch .patch-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
}}
#signal-quality-patch h3 {{
    color: #e0e0e0;
    margin: 0;
}}
#signal-quality-patch .overall-status .label {{
    color: #a0a0a0;
    margin-right: 8px;
}}
#signal-quality-patch .overall-status .value {{
    font-size: 1.4em;
    font-weight: bold;
    margin-right: 12px;
}}
#signal-quality-patch .overall-status .value.healthy {{ color: #4caf50; }}
#signal-quality-patch .overall-status .value.warning {{ color: #ff9800; }}
#signal-quality-patch .overall-status .value.critical {{ color: #f44336; }}
#signal-quality-patch .signal-metrics-table {{
    width: 100%;
    border-collapse: collapse;
    color: #e0e0e0;
}}
#signal-quality-patch .signal-metrics-table th {{
    text-align: left;
    padding: 8px;
    border-bottom: 2px solid #4a4a6a;
    color: #a0a0a0;
}}
#signal-quality-patch .signal-metrics-table td {{
    padding: 8px;
    border-bottom: 1px solid #2a2a4a;
}}
#signal-quality-patch .signal-row.healthy td {{ color: #4caf50; }}
#signal-quality-patch .signal-row.warning td {{ color: #ff9800; }}
#signal-quality-patch .signal-row.critical td {{ color: #f44336; }}
#signal-quality-patch .score-bar {{
    position: relative;
    height: 20px;
    background: #2a2a4a;
    border-radius: 4px;
    overflow: hidden;
}}
#signal-quality-patch .score-fill {{
    height: 100%;
    transition: width 0.3s ease;
}}
#signal-quality-patch .score-fill.healthy {{ background: #4caf50; }}
#signal-quality-patch .score-fill.warning {{ background: #ff9800; }}
#signal-quality-patch .score-fill.critical {{ background: #f44336; }}
#signal-quality-patch .score-text {{
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    font-size: 0.75em;
    color: #fff;
    text-shadow: 0 0 2px #000;
}}
#signal-quality-patch .patch-footer {{
    margin-top: 12px;
    font-size: 0.8em;
    color: #707070;
    display: flex;
    justify-content: space-between;
}}
</style>
"""
    return html

def write_html_patch(patch_content: str) -> bool:
    """Write HTML patch to sentinel status directory."""
    output_path = '/home/workspace/zo_sentinel/signal_quality_patch.html'
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(patch_content)
        logger.info(f"Wrote HTML patch to {output_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to write HTML patch: {e}")
        return False

def cycle() -> Dict[str, Any]:
    """Perform one cycle of signal quality dashboard patching."""
    logger.info("Starting signal quality dashboard patch cycle")
    
    counts = get_signal_distinct_counts()
    logger.info(f"Signal distinct counts: {counts}")
    
    scores = compute_discrimination_score(counts)
    logger.info(f"Discrimination scores computed: overall={scores['overall_coverage_pct']}%")
    
    patch_html = generate_html_patch(scores)
    write_html_patch(patch_html)
    
    # Also update a JSON summary for programmatic consumers
    summary_path = '/home/workspace/zo_sentinel/signal_quality_summary.json'
    import json
    summary_data = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'coverage_pct': scores['overall_coverage_pct'],
        'total_combinations': scores['total_distinct_combinations'],
        'counts': counts,
        'cardinalities': SIGNAL_CARDINALITIES
    }
    try:
        with open(summary_path, 'w', encoding='utf-8') as f:
            json.dump(summary_data, f, indent=2)
        logger.info(f"Wrote JSON summary to {summary_path}")
    except Exception as e:
        logger.error(f"Failed to write JSON summary: {e}")
    
    return scores

def run() -> None:
    """Main daemon loop."""
    import signal
    
    if not check_single_instance():
        sys.exit(1)
    
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info(f"{SERVICE_NAME} starting...")
    
    POLL_SECS = 60
    
    while True:
        try:
            scores = cycle()
            send_heartbeat(
                status='running',
                meta={
                    'coverage_pct': scores['overall_coverage_pct'],
                    'total_combinations': scores['total_distinct_combinations']
                }
            )
        except Exception as e:
            logger.error(f"Cycle failed: {e}")
            send_heartbeat(status='error', meta={'error': str(e)})
        
        import time
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    run()