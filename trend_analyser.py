#!/usr/bin/env python3
"""
trend_analyser.py - ZO-SENTINEL Trend analysis daemon.
Analyses MCP server registry trends over time and generates reports.
"""
import requests
import time
import os
import signal
import sys
from datetime import datetime, timezone, timedelta
from collections import defaultdict

SERVICE_NAME = 'trend_analyser'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
HEARTBEAT_INTERVAL = 43200

REPORT_PATH = '/var/log/zo_sentinel/TREND_REPORT.md'

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
    def cleanup():
        if os.path.exists(pid_file):
            os.remove(pid_file)
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

def send_heartbeat():
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")

def ws_query(sql, params=None):
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()

def ws_write(table, rows, wait=True):
    url = f'{WRITE_SERVICE_URL}/write'
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()

def get_verdict_distribution():
    sql = """
    SELECT 
        COALESCE(verdict, 'PENDING_REVIEW') as verdict,
        COUNT(*) as count
    FROM mcp_server_registry
    GROUP BY verdict
    ORDER BY count DESC
    """
    try:
        result = ws_query(sql)
        data = result.get('data', [])
        distribution = {}
        for row in data:
            verdict = row[0] if len(row) > 0 else 'UNKNOWN'
            count = row[1] if len(row) > 1 else 0
            distribution[verdict] = {'count': count}
        return distribution
    except Exception as e:
        print(f"Error getting verdict distribution: {e}")
        return {}

def get_trust_score_trend():
    trends = {}
    periods = {
        '7d': 7,
        '14d': 14,
        '30d': 30
    }
    for period_name, days in periods.items():
        sql = """
        SELECT 
            AVG(trust_score) as avg_score,
            COUNT(*) as sample_size
        FROM mcp_server_registry
        WHERE last_assessed >= NOW() - INTERVAL '1 day' * ?
          AND trust_score IS NOT NULL
        """
        try:
            result = ws_query(sql, [days])
            data = result.get('data', [])
            if data and len(data[0]) >= 2:
                trends[period_name] = {
                    'avg': data[0][0] if data[0][0] else 0.0,
                    'samples': data[0][1] if data[0][1] else 0
                }
        except Exception as e:
            print(f"Error getting {period_name} trend: {e}")
            trends[period_name] = {'avg': 0.0, 'samples': 0}
        sql_prev = """
        SELECT 
            AVG(trust_score) as avg_score
        FROM mcp_server_registry
        WHERE last_assessed >= NOW() - INTERVAL '1 day' * ?
          AND last_assessed < NOW() - INTERVAL '1 day' * ?
          AND trust_score IS NOT NULL
        """
        try:
            result_prev = ws_query(sql_prev, [days * 2, days])
            data_prev = result_prev.get('data', [])
            if data_prev and data_prev[0] and data_prev[0][0]:
                current = trends[period_name]['avg']
                previous = data_prev[0][0]
                trends[period_name]['change'] = current - previous
            else:
                trends[period_name]['change'] = 0.0
        except Exception:
            trends[period_name]['change'] = 0.0
    return trends

def get_new_servers_per_day():
    sql = """
    SELECT 
        DATE(first_seen) as day,
        COUNT(*) as new_count
    FROM mcp_server_registry
    WHERE first_seen >= NOW() - INTERVAL '30 days'
    GROUP BY DATE(first_seen)
    ORDER BY day DESC
    LIMIT 30
    """
    try:
        result = ws_query(sql)
        data = result.get('data', [])
        daily = {}
        for row in data:
            day = row[0] if len(row) > 0 else None
            count = row[1] if len(row) > 1 else 0
            if day:
                daily[str(day)] = {'count': count}
        return daily
    except Exception as e:
        print(f"Error getting new servers per day: {e}")
        return {}

def get_threat_trends():
    trends = {}
    periods = {
        '7d': 7,
        '14d': 14,
        '30d': 30
    }
    for period_name, days in periods.items():
        sql = """
        SELECT 
            COUNT(*) as threat_count
        FROM mcp_threat_associations
        WHERE reported_at >= NOW() - INTERVAL '1 day' * ?
        """
        try:
            result = ws_query(sql, [days])
            data = result.get('data', [])
            trends[period_name] = {
                'new': data[0][0] if data and data[0] and data[0][0] else 0,
                'total': 0
            }
        except Exception as e:
            print(f"Error getting threat trends for {period_name}: {e}")
            trends[period_name] = {'new': 0, 'total': 0}
        sql_total = "SELECT COUNT(*) FROM mcp_threat_associations"
        try:
            result_total = ws_query(sql_total)
            data_total = result_total.get('data', [])
            trends[period_name]['total'] = data_total[0][0] if data_total and data_total[0] else 0
        except:
            pass
    return trends

def get_risk_tier_distribution():
    sql = """
    SELECT 
        COALESCE(risk_tier, 'UNKNOWN') as risk_tier,
        COUNT(*) as count
    FROM mcp_server_registry
    GROUP BY risk_tier
    ORDER BY count DESC
    """
    try:
        result = ws_query(sql)
        data = result.get('data', [])
        distribution = {}
        for row in data:
            tier = row[0] if len(row) > 0 else 'UNKNOWN'
            count = row[1] if len(row) > 1 else 0
            distribution[tier] = {'count': count}
        return distribution
    except Exception as e:
        print(f"Error getting risk tier distribution: {e}")
        return {}

def get_total_servers():
    sql = "SELECT COUNT(*) FROM mcp_server_registry"
    try:
        result = ws_query(sql)
        data = result.get('data', [])
        return data[0][0] if data and data[0] else 0
    except:
        return 0

def generate_ascii_bar(value, max_val, width=40):
    if max_val == 0:
        return ""
    bar_len = int((value / max_val) * width)
    return "|" * bar_len

def build_trend_report(trend_data):
    total_servers = get_total_servers()
    report_lines = []
    report_lines.append("# ZO-SENTINEL Trend Report")
    report_lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    report_lines.append("")
    report_lines.append("## Executive Summary")
    report_lines.append("")
    report_lines.append(f"- **Total Servers Tracked**: {total_servers}")
    report_lines.append(f"- **Report Period**: Last 30 days")
    report_lines.append("")
    verdict_dist = trend_data.get('verdict_distribution', {})
    if verdict_dist:
        total_verdicts = sum(v['count'] for v in verdict_dist.values())
        report_lines.append(f"- **Verdicts Issued**: {total_verdicts}")
    report_lines.append("")
    report_lines.append("## Verdict Distribution")
    report_lines.append("")
    if verdict_dist:
        max_count = max(v['count'] for v in verdict_dist.values()) if verdict_dist else 1
        for verdict, data in sorted(verdict_dist.items(), key=lambda x: x[1]['count'], reverse=True):
            bar = generate_ascii_bar(data['count'], max_count)
            pct = (data['count'] / total_servers * 100) if total_servers > 0 else 0
            report_lines.append(f"{verdict:30} |{bar} {data['count']:4} ({pct:5.1f}%)")
    else:
        report_lines.append("No verdict data available")
    report_lines.append("")
    report_lines.append("## Trust Score Trend")
    report_lines.append("")
    trust_trend = trend_data.get('trust_score_trend', {})
    if trust_trend:
        report_lines.append("| Period   | Avg Trust Score | Samples | Change   | Trend |")
        report_lines.append("|----------|-----------------|---------|----------|-------|")
        for period, data in sorted(trust_trend.items()):
            change = data.get('change', 0)
            if change > 0.01:
                trend_arrow = "↑ IMPROVING"
            elif change < -0.01:
                trend_arrow = "↓ DECLINING"
            else:
                trend_arrow = "→ STABLE"
            change_str = f"+{change:.3f}" if change >= 0 else f"{change:.3f}"
            report_lines.append(f"| {period:9} | {data.get('avg', 0):15.3f} | {data.get('samples', 0):7} | {change_str:8} | {trend_arrow} |")
    else:
        report_lines.append("No trust score trend data available")
    report_lines.append("")
    report_lines.append("## New Servers Per Day (Last 14 Days)")
    report_lines.append("")
    new_per_day = trend_data.get('new_servers_per_day', {})
    if new_per_day:
        sorted_days = sorted(new_per_day.items(), key=lambda x: x[0], reverse=True)[:14]
        max_new = max(v['count'] for v in new_per_day.values()) if new_per_day else 1
        for day, data in sorted_days:
            bar = generate_ascii_bar(data['count'], max_new)
            report_lines.append(f"{day:12} |{bar} {data['count']:3}")
    else:
        report_lines.append("No new server data available")
    report_lines.append("")
    report_lines.append("## Threat Trends")
    report_lines.append("")
    threat_trends = trend_data.get('threat_trends', {})
    if threat_trends:
        report_lines.append("| Period   | New Threats | Total Threats |")
        report_lines.append("|----------|-------------|---------------|")
        for period, data in sorted(threat_trends.items()):
            report_lines.append(f"| {period:9} | {data.get('new', 0):11} | {data.get('total', 0):14} |")
    else:
        report_lines.append("No threat trend data available")
    report_lines.append("")
    report_lines.append("## Risk Tier Distribution")
    report_lines.append("")
    risk_dist = trend_data.get('risk_tier_distribution', {})
    if risk_dist:
        max_risk = max(v['count'] for v in risk_dist.values()) if risk_dist else 1
        for tier, data in sorted(risk_dist.items(), key=lambda x: x[1]['count'], reverse=True):
            bar = generate_ascii_bar(data['count'], max_risk)
            pct = (data['count'] / total_servers * 100) if total_servers > 0 else 0
            report_lines.append(f"{tier:20} |{bar} {data['count']:4} ({pct:5.1f}%)")
    else:
        report_lines.append("No risk tier data available")
    return "\n".join(report_lines)

def identify_signals(trend_data):
    signals = []
    trust_trend = trend_data.get('trust_score_trend', {})
    if trust_trend:
        change_7d = trust_trend.get('7d', {}).get('change', 0)
        if change_7d > 0.02:
            signals.append({
                'signal_type': 'improving_security',
                'severity': 'info',
                'metric': 'trust_score',
                'period': '7d',
                'value': change_7d,
                'description': f'Trust score trending upward (+{change_7d:.3f} over 7 days)'
            })
        elif change_7d < -0.02:
            signals.append({
                'signal_type': 'degrading_security',
                'severity': 'warning',
                'metric': 'trust_score',
                'period': '7d',
                'value': change_7d,
                'description': f'Trust score trending downward ({change_7d:.3f} over 7 days)'
            })
    new_per_day = trend_data.get('new_servers_per_day', {})
    if new_per_day:
        recent_days = sorted(new_per_day.keys(), reverse=True)[:3]
        if len(recent_days) >= 2:
            avg_new = sum(new_per_day[d]['count'] for d in recent_days) / len(recent_days)
            if avg_new > 5:
                signals.append({
                    'signal_type': 'rapid_growth',
                    'severity': 'warning',
                    'metric': 'new_servers',
                    'period': '3d',
                    'value': avg_new,
                    'description': f'Rapid MCP server growth: {avg_new:.1f} new servers/day'
                })
    threat_trends = trend_data.get('threat_trends', {})
    if threat_trends:
        threats_7d = threat_trends.get('7d', {}).get('new', 0)
        threats_14d = threat_trends.get('14d', {}).get('new', 0)
        if threats_14d > 0 and threats_7d > (threats_14d / 2):
            signals.append({
                'signal_type': 'elevated_threat_activity',
                'severity': 'warning',
                'metric': 'threats',
                'period': '7d',
                'value': threats_7d,
                'description': f'Elevated threat activity: {threats_7d} new threats in last 7 days'
            })
    return signals

def write_mesh_event(event_type, summary, details):
    try:
        ws_write('mesh_events', {
            'event_type': event_type,
            'summary': summary,
            'details': details,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'source_service': SERVICE_NAME
        })
    except Exception as e:
        print(f"Error writing mesh event: {e}")

def save_report(content):
    try:
        os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
        with open(REPORT_PATH, 'w') as f:
            f.write(content)
        print(f"Trend report saved to {REPORT_PATH}")
    except Exception as e:
        print(f"Error saving report: {e}")

def cycle():
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting trend analysis cycle...")
    trend_data = {
        'verdict_distribution': get_verdict_distribution(),
        'trust_score_trend': get_trust_score_trend(),
        'new_servers_per_day': get_new_servers_per_day(),
        'threat_trends': get_threat_trends(),
        'risk_tier_distribution': get_risk_tier_distribution()
    }
    signals = identify_signals(trend_data)
    report = build_trend_report(trend_data)
    save_report(report)
    if signals:
        for sig in signals:
            print(f"Signal detected: {sig['signal_type']} - {sig['description']}")
            write_mesh_event('trend_signal', sig['description'], sig)
    else:
        write_mesh_event('trend_report', 'Trend analysis completed - no significant signals', trend_data)
    print(f"[{datetime.now(timezone.utc).isoformat()}] Trend analysis cycle complete")
    return trend_data, signals

def run():
    check_single_instance()
    print(f"Starting {SERVICE_NAME} daemon...")
    send_heartbeat()
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in cycle: {e}")
            write_mesh_event('trend_error', f'Cycle failed: {str(e)}', {'error': str(e)})
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

if __name__ == '__main__':
    run()