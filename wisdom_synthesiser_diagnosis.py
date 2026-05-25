import logging
import sqlite3
import sys
import os
from datetime import datetime, timezone
from pathlib import Path

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'wisdom_synthesiser_diagnosis'
STALE_THRESHOLD_SECONDS = 14400
MESH_MEMORY_DB = '/home/workspace/Datasets/zo-mesh/mesh_memory.db'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def ws_query(sql, params=None):
    import requests
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])


def ws_write(table, rows):
    import requests
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def query_service_health():
    sql = """
    SELECT service_name, status, last_heartbeat, meta
    FROM service_health
    WHERE service_name LIKE '%wisdom%'
    ORDER BY last_heartbeat DESC
    LIMIT 10
    """
    return ws_query(sql)


def query_mcp_registry_for_wisdom():
    sql = """
    SELECT server_id, server_name, url, status, last_seen, last_scanned, meta
    FROM mcp_server_registry
    WHERE server_name LIKE '%wisdom%'
    ORDER BY last_seen DESC
    LIMIT 10
    """
    return ws_query(sql)


def check_mesh_memory_wisdom():
    conn = sqlite3.connect(MESH_MEMORY_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    rows = []
    try:
        cursor.execute("""
            SELECT name, type FROM sqlite_master 
            WHERE name LIKE '%wisdom%' OR name LIKE '%synthes%'
        """)
        tables = cursor.fetchall()
        for tbl in tables:
            try:
                cursor.execute(f"SELECT * FROM {tbl['name']} LIMIT 5")
                sample = cursor.fetchall()
                rows.append({
                    'table': tbl['name'],
                    'type': tbl['type'],
                    'sample_count': len(sample),
                    'sample': [dict(r) for r in sample]
                })
            except Exception as e:
                rows.append({
                    'table': tbl['name'],
                    'type': tbl['type'],
                    'error': str(e)
                })
    finally:
        conn.close()
    return rows


def check_log_for_wisdom_errors():
    log_path = '/home/workspace/logs/wisdom_synthesiser.log'
    errors = []
    if os.path.exists(log_path):
        with open(log_path, 'r') as f:
            lines = f.readlines()
            recent_lines = lines[-100:]
            for line in recent_lines:
                if 'ERROR' in line or 'Exception' in line or 'Failed' in line:
                    errors.append(line.strip())
    return errors


def parse_heartbeat_age(last_heartbeat_str):
    if not last_heartbeat_str:
        return None
    try:
        if last_heartbeat_str.endswith('Z'):
            last_heartbeat_str = last_heartbeat_str[:-1]
        last_ts = datetime.fromisoformat(last_heartbeat_str)
        now = datetime.now(timezone.utc)
        age_seconds = (now - last_ts).total_seconds()
        return age_seconds
    except Exception:
        return None


def run_diagnosis():
    logger.info("Starting wisdom_synthesiser staleness diagnosis")
    findings = {
        'diagnosis_ts': datetime.now(timezone.utc).isoformat(),
        'stale_threshold_seconds': STALE_THRESHOLD_SECONDS,
        'service_health_entries': [],
        'mcp_registry_entries': [],
        'mesh_memory_tables': [],
        'recent_log_errors': [],
        'diagnosis': None
    }

    service_entries = query_service_health()
    findings['service_health_entries'] = service_entries

    if service_entries:
        for entry in service_entries:
            entry_name = entry.get('service_name', '')
            if 'wisdom' in entry_name.lower():
                age = parse_heartbeat_age(entry.get('last_heartbeat'))
                entry['age_seconds'] = age
                entry['is_stale'] = age > STALE_THRESHOLD_SECONDS if age else True
                logger.info(f"Found service: {entry_name}, age={age}s, stale={entry.get('is_stale')}")

    registry_entries = query_mcp_registry_for_wisdom()
    findings['mcp_registry_entries'] = registry_entries

    mesh_tables = check_mesh_memory_wisdom()
    findings['mesh_memory_tables'] = mesh_tables

    log_errors = check_log_for_wisdom_errors()
    findings['recent_log_errors'] = log_errors

    if log_errors:
        logger.warning(f"Found {len(log_errors)} errors in wisdom_synthesiser log")
    else:
        logger.info("No errors found in recent wisdom_synthesiser log")

    staleness_reasons = []
    if not service_entries:
        staleness_reasons.append("No service_health entries found for wisdom_synthesiser")
    elif all(e.get('is_stale', True) for e in service_entries if 'wisdom' in e.get('service_name', '').lower()):
        staleness_reasons.append("All wisdom_synthesiser heartbeats exceed stale threshold")
    if log_errors:
        staleness_reasons.append(f"Recent log errors detected: {len(log_errors)} error lines")

    findings['diagnosis'] = {
        'is_stale': len(staleness_reasons) > 0,
        'reasons': staleness_reasons,
        'recommendations': []
    }

    if not service_entries:
        findings['diagnosis']['recommendations'].append(
            "wisdom_synthesiser daemon may not be running. Check process list with pgrep -f wisdom_synthesiser"
        )
    if log_errors:
        findings['diagnosis']['recommendations'].append(
            "Daemon produced log errors. Review recent_log_errors for details"
        )
    if not findings['diagnosis']['reasons']:
        findings['diagnosis']['diagnosis'] = "No staleness detected"
        findings['diagnosis']['is_stale'] = False

    logger.info(f"Diagnosis complete: is_stale={findings['diagnosis']['is_stale']}")
    logger.info(f"Reasons: {findings['diagnosis'].get('reasons', [])}")

    ws_write('diagnostic_results', [{
        'diagnostic_name': SERVICE_NAME,
        'target_service': 'wisdom_synthesiser',
        'diagnosis_ts': findings['diagnosis_ts'],
        'is_stale': findings['diagnosis']['is_stale'],
        'findings_json': str(findings)
    }])

    print(f"\n=== wisdom_synthesiser Staleness Diagnosis ===")
    print(f"Stale threshold: {STALE_THRESHOLD_SECONDS}s ({STALE_THRESHOLD_SECONDS/3600:.1f}h)")
    print(f"Service health entries: {len(service_entries)}")
    print(f"MCP registry entries: {len(registry_entries)}")
    print(f"Log errors found: {len(log_errors)}")
    print(f"Stale: {findings['diagnosis']['is_stale']}")
    if findings['diagnosis'].get('reasons'):
        print("Reasons:")
        for r in findings['diagnosis']['reasons']:
            print(f"  - {r}")
    if findings['diagnosis'].get('recommendations'):
        print("Recommendations:")
        for rec in findings['diagnosis']['recommendations']:
            print(f"  - {rec}")

    return findings


if __name__ == '__main__':
    try:
        run_diagnosis()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Diagnosis failed: {e}")
        sys.exit(1)