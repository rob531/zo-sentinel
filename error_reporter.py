#!/usr/bin/env python3
"""
error_reporter.py -- ZO-SENTINEL Error Reporter Daemon.
Generates daily error reports from mesh_events table.
"""
import requests
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

SERVICE_NAME = 'error_reporter'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
HEARTBEAT_INTERVAL = 60
REPORT_INTERVAL = 86400

ERROR_EVENT_TYPES = (
    'build_failed',
    'build_generation_failed',
    'smoke_fail',
    'signal_drift_detected',
    'assessment_quality_issue'
)

def ws_query(sql, params=None):
    """Execute SQL query against DuckDB via inference_router."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_write(table, rows, wait=True):
    """Write rows to DuckDB table via write_service."""
    url = f'{WRITE_SERVICE_URL}/write'
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat():
    """Send service heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")

def get_recent_errors():
    """Fetch error events from the last 24 hours."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    
    placeholders = ','.join(['?' for _ in ERROR_EVENT_TYPES])
    sql = f"""
    SELECT 
        event_type,
        agent_id,
        error_message,
        error_pattern,
        severity,
        created_at
    FROM mesh_events
    WHERE event_type IN ({placeholders})
    AND created_at > ?
    ORDER BY created_at DESC
    """
    
    params = list(ERROR_EVENT_TYPES) + [since]
    
    try:
        resp = requests.post(EXECUTE_URL, json={'sql': sql, 'params': params}, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get('data', [])
    except Exception as e:
        print(f"Failed to fetch recent errors: {e}")
        return []

def get_previous_day_errors():
    """Fetch error events from previous 24h period for trend comparison."""
    now = datetime.now(timezone.utc)
    prev_day_start = (now - timedelta(days=2)).isoformat()
    prev_day_end = (now - timedelta(days=1)).isoformat()
    
    placeholders = ','.join(['?' for _ in ERROR_EVENT_TYPES])
    sql = f"""
    SELECT 
        event_type,
        agent_id,
        COUNT(*) as count
    FROM mesh_events
    WHERE event_type IN ({placeholders})
    AND created_at >= ?
    AND created_at < ?
    GROUP BY event_type, agent_id
    """
    
    params = list(ERROR_EVENT_TYPES) + [prev_day_start, prev_day_end]
    
    try:
        resp = requests.post(EXECUTE_URL, json={'sql': sql, 'params': params}, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get('data', [])
    except Exception as e:
        print(f"Failed to fetch previous day errors: {e}")
        return []

def group_errors_by_type(events):
    """Group errors by event_type and count occurrences."""
    grouped = {}
    for event in events:
        event_type = event.get('event_type', 'unknown')
        if event_type not in grouped:
            grouped[event_type] = {'count': 0, 'events': [], 'agents': set()}
        grouped[event_type]['count'] += 1
        grouped[event_type]['events'].append(event)
        if event.get('agent_id'):
            grouped[event_type]['agents'].add(event['agent_id'])
    return grouped

def group_errors_by_agent(events):
    """Group errors by agent_id and count occurrences."""
    grouped = {}
    for event in events:
        agent_id = event.get('agent_id', 'unknown')
        if agent_id not in grouped:
            grouped[agent_id] = {'count': 0, 'events': [], 'types': set()}
        grouped[agent_id]['count'] += 1
        grouped[agent_id]['events'].append(event)
        if event.get('event_type'):
            grouped[agent_id]['types'].add(event['event_type'])
    return grouped

def extract_failure_patterns(events):
    """Extract and count failure patterns from error messages."""
    patterns = {}
    
    for event in events:
        error_msg = event.get('error_message', '') or ''
        error_type = event.get('event_type', 'unknown')
        error_pattern = event.get('error_pattern', '')
        
        if error_pattern:
            pattern_key = error_pattern[:80]
        elif error_msg:
            words = error_msg.split()
            pattern_key = ' '.join(words[:10]) if len(words) > 10 else error_msg
        else:
            pattern_key = error_type
        
        if pattern_key not in patterns:
            patterns[pattern_key] = {
                'count': 0,
                'type': error_type,
                'example': error_msg[:200] if error_msg else '',
                'agents': set()
            }
        
        patterns[pattern_key]['count'] += 1
        if event.get('agent_id'):
            patterns[pattern_key]['agents'].add(event['agent_id'])
    
    return sorted(patterns.values(), key=lambda x: x['count'], reverse=True)

def calculate_trend(today_total, prev_total):
    """Calculate trend direction and percentage."""
    if prev_total == 0:
        return 'NEW' if today_total > 0 else 'NONE', 0.0
    
    pct_change = ((today_total - prev_total) / prev_total) * 100
    
    if pct_change > 10:
        return 'DEGRADED', pct_change
    elif pct_change < -10:
        return 'IMPROVED', pct_change
    else:
        return 'STABLE', pct_change

def generate_report_content(by_type, by_agent, patterns, prev_errors, timestamp):
    """Generate markdown report content."""
    
    prev_by_type = {}
    for row in prev_errors:
        event_type = row.get('event_type', 'unknown')
        if event_type not in prev_by_type:
            prev_by_type[event_type] = 0
        prev_by_type[event_type] += row.get('count', 0)
    
    total_today = sum(d['count'] for d in by_type.values())
    total_prev = sum(prev_by_type.values())
    trend, trend_pct = calculate_trend(total_today, total_prev)
    
    trend_icon = '📈' if trend == 'DEGRADED' else '📉' if trend == 'IMPROVED' else '➡️'
    
    report_lines = [
        "# ZO-SENTINEL Error Report",
        "",
        f"**Generated:** {timestamp}",
        f"**Period:** Last 24 hours",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Errors | {total_today} |",
        f"| Previous Period | {total_prev} |",
        f"| Trend | {trend} ({trend_pct:+.1f}%) {trend_icon} |",
        f"| Error Types | {len(by_type)} |",
        f"| Affected Agents | {len(by_agent)} |",
        "",
        "---",
        "",
        "## Errors by Type",
        "",
        f"| Event Type | Count | Prev | Change | Agents |",
        f"|------------|-------|------|--------|--------|",
    ]
    
    for event_type, data in sorted(by_type.items(), key=lambda x: x[1]['count'], reverse=True):
        prev_count = prev_by_type.get(event_type, 0)
        change = data['count'] - prev_count
        change_str = f"{change:+d}"
        agent_count = len(data['agents'])
        report_lines.append(f"| {event_type} | {data['count']} | {prev_count} | {change_str} | {agent_count} |")
    
    report_lines.extend([
        "",
        "---",
        "",
        "## Top 5 Recurring Failures",
        "",
        f"| Rank | Error Pattern | Count | Type | Agents |",
        f"|------|---------------|-------|------|--------|",
    ])
    
    for i, pattern in enumerate(patterns[:5], 1):
        pattern_text = pattern['example'][:60].replace('|', '\\|').replace('\n', ' ')
        if len(pattern['example']) > 60:
            pattern_text += '...'
        elif not pattern_text:
            pattern_text = '(no details)'
        agent_count = len(pattern['agents'])
        report_lines.append(f"| {i} | {pattern_text} | {pattern['count']} | {pattern['type']} | {agent_count} |")
    
    report_lines.extend([
        "",
        "---",
        "",
        "## Affected Agents (Top 10)",
        "",
        f"| Agent ID | Error Count | Error Types |",
        f"|----------|-------------|-------------|",
    ])
    
    for agent_id, data in sorted(by_agent.items(), key=lambda x: x[1]['count'], reverse=True)[:10]:
        types_str = ', '.join(sorted(data['types'])[:3])
        report_lines.append(f"| {agent_id} | {data['count']} | {types_str} |")
    
    report_lines.extend([
        "",
        "---",
        "",
        "## Correction Actions",
        "",
        "| Action | Description | Status |",
        "|--------|-------------|--------|",
    ])
    
    for event_type, data in by_type.items():
        if data['count'] > 5:
            report_lines.append(f"| Pattern Review | Investigate {event_type} ({data['count']} occurrences) | Required |")
    
    high_failure_agents = [(aid, d) for aid, d in by_agent.items() if d['count'] > 10]
    if high_failure_agents:
        for aid, data in high_failure_agents[:3]:
            report_lines.append(f"| Agent Isolation | Flag {aid} with {data['count']} failures | Required |")
    
    report_lines.extend([
        "",
        "---",
        "",
        "## Trend Analysis",
        "",
    ])
    
    if trend == 'IMPROVED':
        report_lines.append("✅ Error count has decreased compared to the previous 24-hour period.")
    elif trend == 'DEGRADED':
        report_lines.append("⚠️ Error count has increased compared to the previous 24-hour period.")
    else:
        report_lines.append("ℹ️ Error count remains stable compared to the previous 24-hour period.")
    
    report_lines.extend([
        "",
        "---",
        "",
        "*Report generated by ZO-SENTINEL Error Reporter*",
    ])
    
    return '\n'.join(report_lines)

def write_report_file(content):
    """Write report to ERROR_REPORT.md file."""
    report_path = Path('/home/workspace/zo_sentinel/ERROR_REPORT.md')
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(content)
        print(f"Report written to {report_path}")
        return True
    except Exception as e:
        print(f"Failed to write report: {e}")
        return False

def save_report_summary(by_type, by_agent, patterns, total_today, total_prev, trend):
    """Save summary stats to mesh_events."""
    top_patterns = ','.join([f"{p['count']}:{p['type']}" for p in patterns[:5]])
    top_types = ','.join([f"{k}:{v['count']}" for k, v in sorted(by_type.items(), key=lambda x: x[1]['count'], reverse=True)[:5]])
    
    summary_row = {
        'event_type': 'error_report_generated',
        'agent_id': SERVICE_NAME,
        'total_errors': total_today,
        'prev_errors': total_prev,
        'trend': trend,
        'error_type_count': len(by_type),
        'affected_agents': len(by_agent),
        'top_error_types': top_types,
        'top_patterns': top_patterns,
        'created_at': datetime.now(timezone.utc).isoformat()
    }
    
    try:
        ws_write('mesh_events', summary_row)
        print("Report summary saved to mesh_events")
    except Exception as e:
        print(f"Failed to save report summary: {e}")

def cycle():
    """Main work cycle."""
    print("Starting error report cycle...")
    
    timestamp = datetime.now(timezone.utc).isoformat()
    
    recent_errors = get_recent_errors()
    print(f"Fetched {len(recent_errors)} recent errors")
    
    prev_errors = get_previous_day_errors()
    print(f"Fetched {len(prev_errors)} previous day errors")
    
    by_type = group_errors_by_type(recent_errors)
    by_agent = group_errors_by_agent(recent_errors)
    patterns = extract_failure_patterns(recent_errors)
    
    total_today = sum(d['count'] for d in by_type.values())
    total_prev = sum(row.get('count', 0) for row in prev_errors)
    trend, _ = calculate_trend(total_today, total_prev)
    
    report_content = generate_report_content(by_type, by_agent, patterns, prev_errors, timestamp)
    
    if write_report_file(report_content):
        save_report_summary(by_type, by_agent, patterns, total_today, total_prev, trend)
    
    print(f"Cycle complete: {total_today} errors across {len(by_type)} types, {len(by_agent)} agents, trend: {trend}")
    return total_today, trend

def run():
    """Main daemon entry point."""
    print(f"Starting {SERVICE_NAME}...")
    
    send_heartbeat()
    
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in cycle: {e}")
        
        send_heartbeat()
        time.sleep(REPORT_INTERVAL)

if __name__ == '__main__':
    run()