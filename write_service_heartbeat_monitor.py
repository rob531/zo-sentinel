import logging
import os
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
LOG = logging.getLogger('write_service_heartbeat_monitor')

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
SERVICE_NAME = 'write_service_heartbeat_monitor'

HTTP_TIMEOUT = 10


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql):
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        LOG.error(f"ws_query failed: {e}")
        return {'rows': []}


def ws_write(table, rows):
    try:
        resp = requests.post(
            WRITE_SERVICE_URL + '/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        LOG.error(f"ws_write failed: {e}")
        return None


def check_health_endpoint():
    try:
        resp = requests.get(WRITE_SERVICE_URL + '/health', timeout=HTTP_TIMEOUT)
        if resp.status_code == 200:
            return resp.json()
        else:
            return {'error': f'HTTP {resp.status_code}', 'text': resp.text[:200]}
    except Exception as e:
        return {'error': str(e)}


def get_service_health_record(service=None):
    sql = "SELECT service, last_heartbeat FROM service_health"
    if service:
        sql += f" WHERE service = '{service}'"
    result = ws_query(sql)
    if result and 'rows' in result:
        return result['rows']
    return []


def get_write_service_server_record():
    result = ws_query("""
        SELECT server_id, name, last_seen, last_assessed
        FROM mcp_server_registry
        WHERE name = 'write_service'
           OR url LIKE '%8772%'
        LIMIT 5
    """)
    if result and 'rows' in result:
        return result['rows']
    return []


def verify_mcp_tables_access():
    tables = [
        ('mcp_server_registry', 'SELECT COUNT(*) as cnt FROM mcp_server_registry'),
        ('mcp_signal_scores', 'SELECT COUNT(*) as cnt FROM mcp_signal_scores'),
        ('mcp_attestations', 'SELECT COUNT(*) as cnt FROM mcp_attestations'),
        ('service_health', 'SELECT COUNT(*) as cnt FROM service_health'),
    ]
    results = {}
    for table, sql in tables:
        result = ws_query(sql)
        if result and 'rows' in result:
            cnt = result['rows'][0].get('cnt', 0) if result['rows'] else 0
            results[table] = {'accessible': True, 'count': cnt}
        else:
            results[table] = {'accessible': False, 'error': 'query failed'}
    return results


def parse_iso(ts):
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    try:
        ts_str = str(ts)
        ts_str = ts_str.replace('Z', '+00:00')
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def compute_age_seconds(ts):
    if not ts:
        return None
    parsed = parse_iso(ts)
    if not parsed:
        return None
    now = datetime.now(timezone.utc)
    delta = now - parsed
    return delta.total_seconds()


def format_duration(seconds):
    if seconds is None:
        return 'N/A'
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f'{hours}h{minutes}m{secs}s'
    elif minutes > 0:
        return f'{minutes}m{secs}s'
    else:
        return f'{secs}s'


def run():
    LOG.info("=" * 60)
    LOG.info("WriteService Heartbeat Investigator")
    LOG.info(f"Run at: {utc_now_iso()}")
    LOG.info("=" * 60)

    diagnostics = {
        'run_at': utc_now_iso(),
        'health_endpoint': None,
        'health_endpoint_age': None,
        'service_health_record': {},
        'service_health_age': None,
        'last_seen_discrepancy': None,
        'table_access': {},
        'summary': {},
        'recommendations': []
    }

    LOG.info("\n[1/5] Checking /health endpoint directly...")
    health_data = check_health_endpoint()
    diagnostics['health_endpoint'] = health_data
    LOG.info(f"  /health response: {health_data}")

    health_ts = health_data.get('last_heartbeat') or health_data.get('ts') or health_data.get('timestamp')
    if health_ts:
        health_age = compute_age_seconds(health_ts)
        diagnostics['health_endpoint_age'] = health_age
        LOG.info(f"  Health endpoint age: {format_duration(health_age)}")

        if health_age and health_age > 300:
            diagnostics['summary']['health_endpoint_stale'] = True
            diagnostics['recommendations'].append('write_service process may be wedged - /health endpoint itself is stale')
        else:
            diagnostics['summary']['health_endpoint_stale'] = False
    else:
        LOG.warning("  Could not extract timestamp from /health endpoint")
        diagnostics['health_endpoint_age'] = None

    LOG.info("\n[2/5] Querying service_health table...")
    service_records = get_service_health_record()
    diagnostics['service_health_record'] = service_records
    LOG.info(f"  Found {len(service_records)} service_health records")
    for rec in service_records:
        svc = rec.get('service', 'unknown')
        hb = rec.get('last_heartbeat', 'N/A')
        age = compute_age_seconds(hb)
        diagnostics['service_health_age'] = age
        LOG.info(f"  Service '{svc}': last_heartbeat={hb}, age={format_duration(age)}")

    LOG.info("\n[3/5] Checking write_service last_seen in mcp_server_registry...")
    ws_records = get_write_service_server_record()
    diagnostics['mcp_server_registry_write_service'] = ws_records
    LOG.info(f"  Found {len(ws_records)} write_service entries")
    for rec in ws_records:
        ls = rec.get('last_seen', 'N/A')
        age = compute_age_seconds(ls)
        LOG.info(f"  server_id={rec.get('server_id','?')} name={rec.get('name','?')}: last_seen={ls}, age={format_duration(age)}")

    LOG.info("\n[4/5] Verifying DuckDB table access...")
    table_access = verify_mcp_tables_access()
    diagnostics['table_access'] = table_access
    for table, info in table_access.items():
        status = 'OK' if info.get('accessible') else 'FAIL'
        cnt = info.get('count', '?')
        LOG.info(f"  {table}: {status} (count={cnt})")

    all_accessible = all(v.get('accessible', False) for v in table_access.values())
    diagnostics['summary']['duckdb_access_working'] = all_accessible

    LOG.info("\n[5/5] Computing discrepancy analysis...")

    health_age = diagnostics.get('health_endpoint_age')
    service_health_age = diagnostics.get('service_health_age')

    if health_age is not None and service_health_age is not None:
        discrepancy = abs(health_age - service_health_age)
        diagnostics['last_seen_discrepancy'] = discrepancy
        LOG.info(f"  Health endpoint age: {format_duration(health_age)}")
        LOG.info(f"  service_health last_heartbeat age: {format_duration(service_health_age)}")
        LOG.info(f"  Discrepancy: {format_duration(discrepancy)}")

        if discrepancy > 60:
            diagnostics['recommendations'].append(
                f'Mismatch detected: /health shows {format_duration(health_age)} but service_health shows {format_duration(service_health_age)}. '
                'Possible cause: service_health writer is failing while HTTP server continues to respond.'
            )
    elif health_age is None and service_health_age is not None:
        diagnostics['recommendations'].append(
            'Cannot parse /health timestamp but service_health is accessible. '
            'write_service may be running but heartbeat column in /health response is malformed.'
        )
    elif health_age is not None and service_health_age is None:
        diagnostics['recommendations'].append(
            '/health is accessible but write_service has no entry in service_health. '
            'The heartbeat writer may have stopped.'
        )

    if diagnostics['summary'].get('health_endpoint_stale') and diagnostics['summary'].get('duckdb_access_working'):
        diagnostics['recommendations'].append(
            'PARADOX: write_service is writing to DuckDB (tables accessible) but its own heartbeat is stale. '
            'The process may be alive but the heartbeat loop is blocked or the service_health write is failing silently.'
        )

    LOG.info("\n" + "=" * 60)
    LOG.info("DIAGNOSTIC SUMMARY")
    LOG.info("=" * 60)
    LOG.info(f"  /health endpoint stale:     {diagnostics['summary'].get('health_endpoint_stale', 'unknown')}")
    LOG.info(f"  DuckDB access working:      {diagnostics['summary'].get('duckdb_access_working', 'unknown')}")
    LOG.info(f"  Health endpoint age:         {format_duration(health_age)}")
    LOG.info(f"  service_health age:          {format_duration(service_health_age)}")
    LOG.info(f"  Discrepancy:                 {format_duration(discrepancy if 'discrepancy' in locals() else None)}")

    LOG.info("\n  RECOMMENDATIONS:")
    if diagnostics['recommendations']:
        for i, rec in enumerate(diagnostics['recommendations'], 1):
            LOG.info(f"    {i}. {rec}")
    else:
        LOG.info("    No specific issues detected.")

    LOG.info("\n" + "=" * 60)

    try:
        ws_write('diagnostic_reports', [{
            'diagnostic_type': 'write_service_heartbeat_staleness',
            'run_at': diagnostics['run_at'],
            'health_endpoint_stale': diagnostics['summary'].get('health_endpoint_stale'),
            'duckdb_access_working': diagnostics['summary'].get('duckdb_access_working'),
            'health_endpoint_age_seconds': health_age,
            'service_health_age_seconds': service_health_age,
            'discrepancy_seconds': diagnostics.get('last_seen_discrepancy'),
            'recommendations': ' | '.join(diagnostics['recommendations']),
            'table_access_summary': str({k: v.get('count', 0) for k, v in table_access.items()}),
        }])
        LOG.info("Diagnostic report written to diagnostic_reports table.")
    except Exception as e:
        LOG.warning(f"Could not write diagnostic report: {e}")

    LOG.info("\nDiagnostic complete.")
    sys.exit(0)


if __name__ == '__main__':
    import requests as _requests
    requests = _requests
    run()