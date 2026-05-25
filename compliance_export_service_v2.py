import logging
import os
import sys
import csv
import json
from datetime import datetime, timezone
from io import StringIO

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/compliance_export_service_v2.log')]
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'compliance_export_service_v2'
PORT = None
PID_FILE = '/tmp/compliance_export_service_v2.pid'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_URL = 'http://127.0.0.1:8772/query'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'


def ws_query(sql):
    payload = {'sql': sql}
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def check_single_instance():
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        if old_pid and old_pid != pid:
            try:
                os.kill(int(old_pid), 0)
                log.error('Instance already running with PID %s', old_pid)
                sys.exit(1)
            except (OSError, ValueError):
                log.warning('Stale PID file found, overwriting')
    with open(PID_FILE, 'w') as f:
        f.write(pid)


def remove_pid_file():
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    log.info('Received signal %d, shutting down', signum)
    remove_pid_file()
    sys.exit(0)


def get_mcp_servers(server_ids=None):
    if server_ids is None or len(server_ids) == 0:
        sql = "SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count FROM mcp_server_registry"
    else:
        ids = "', '".join(server_ids)
        sql = f"SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count FROM mcp_server_registry WHERE server_id IN ('{ids}')"
    return ws_query(sql)


def get_signal_scores(server_ids):
    if not server_ids:
        return []
    ids = "', '".join(server_ids)
    sql = f"SELECT server_id, signal_name, score, evidence, scored_at FROM mcp_signal_scores WHERE server_id IN ('{ids}')"
    return ws_query(sql)


def get_attestations(server_ids):
    if not server_ids:
        return []
    ids = "', '".join(server_ids)
    sql = f"SELECT server_id, attestation_id, attested_by, attestation_level, expires_at, attested_at FROM mcp_attestations WHERE server_id IN ('{ids}')"
    return ws_query(sql)


def get_risk_register(server_ids):
    if not server_ids:
        return []
    ids = "', '".join(server_ids)
    sql = f"SELECT server_id, risk_tier, risk_rank, threat_count, computed_at FROM mcp_risk_register WHERE server_id IN ('{ids}')"
    return ws_query(sql)


def get_threat_associations(server_ids):
    if not server_ids:
        return []
    ids = "', '".join(server_ids)
    sql = f"SELECT server_id, threat_type, severity, evidence, reported_at FROM mcp_threat_associations WHERE server_id IN ('{ids}')"
    return ws_query(sql)


def filter_by_date_range(records, date_field, date_range):
    if date_range is None:
        return records
    start_date, end_date = date_range
    filtered = []
    for record in records:
        date_val = record.get(date_field)
        if not date_val:
            continue
        try:
            if date_val.endswith('Z'):
                date_val = date_val[:-1]
            record_dt = datetime.fromisoformat(date_val.replace('Z', '+00:00'))
            start_dt = datetime.fromisoformat(start_date.replace('Z', '+00:00')) if isinstance(start_date, str) and 'Z' in start_date else datetime.fromisoformat(start_date)
            end_dt = datetime.fromisoformat(end_date.replace('Z', '+00:00')) if isinstance(end_date, str) and 'Z' in end_date else datetime.fromisoformat(end_date)
            if start_dt <= record_dt <= end_dt:
                filtered.append(record)
        except (ValueError, TypeError):
            filtered.append(record)
    return filtered


def build_compliance_record(server, signals, attestations, risk_records, threats, date_range):
    signal_map = {}
    for s in signals:
        sid = s.get('server_id')
        if sid not in signal_map:
            signal_map[sid] = []
        signal_map[sid].append({
            'signal_name': s.get('signal_name'),
            'score': s.get('score'),
            'scored_at': s.get('scored_at')
        })
    att_map = {}
    for a in attestations:
        sid = a.get('server_id')
        if sid not in att_map:
            att_map[sid] = []
        att_map[sid].append({
            'attestation_id': a.get('attestation_id'),
            'attested_by': a.get('attested_by'),
            'attestation_level': a.get('attestation_level'),
            'expires_at': a.get('expires_at'),
            'attested_at': a.get('attested_at')
        })
    risk_map = {}
    for r in risk_records:
        risk_map[r.get('server_id')] = {
            'risk_tier': r.get('risk_tier'),
            'risk_rank': r.get('risk_rank'),
            'threat_count': r.get('threat_count'),
            'computed_at': r.get('computed_at')
        }
    sid = server.get('server_id')
    record = {
        'server_id': sid,
        'name': server.get('name'),
        'url': server.get('url'),
        'description': server.get('description'),
        'trust_score': server.get('trust_score'),
        'verdict': server.get('verdict'),
        'registry_source': server.get('registry_source'),
        'scan_count': server.get('scan_count'),
        'signals': signal_map.get(sid, []),
        'attestations': att_map.get(sid, []),
        'risk': risk_map.get(sid),
        'threat_associations': [t for t in threats if t.get('server_id') == sid],
        'exported_at': utc_now_iso()
    }
    return record


def export_compliance_report(server_ids=None, format='json', date_range=None):
    log.info('Starting compliance export: server_ids=%s, format=%s, date_range=%s', server_ids, format, date_range)
    servers = get_mcp_servers(server_ids)
    if not servers:
        log.warning('No servers found matching criteria')
        if format == 'csv':
            return ''
        return {'servers': [], 'total': 0, 'exported_at': utc_now_iso()}
    sids = [s.get('server_id') for s in servers]
    signals = filter_by_date_range(get_signal_scores(sids), 'scored_at', date_range)
    attestations = filter_by_date_range(get_attestations(sids), 'attested_at', date_range)
    risk_records = get_risk_register(sids)
    threats = filter_by_date_range(get_threat_associations(sids), 'reported_at', date_range)
    records = []
    for server in servers:
        records.append(build_compliance_record(server, signals, attestations, risk_records, threats, date_range))
    if format == 'csv':
        return format_csv(records)
    result = {
        'servers': records,
        'total': len(records),
        'exported_at': utc_now_iso(),
        'filters_applied': {
            'server_ids': server_ids,
            'date_range': list(date_range) if date_range else None
        }
    }
    log.info('Export complete: %d records', len(records))
    return result


def format_csv(records):
    if not records:
        return ''
    output = StringIO()
    fieldnames = [
        'server_id', 'name', 'url', 'description', 'trust_score', 'verdict',
        'registry_source', 'scan_count', 'risk_tier', 'risk_rank', 'threat_count',
        'signal_count', 'attestation_count', 'threat_count_detail', 'exported_at'
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for rec in records:
        row = {
            'server_id': rec.get('server_id', ''),
            'name': rec.get('name', ''),
            'url': rec.get('url', ''),
            'description': rec.get('description', ''),
            'trust_score': rec.get('trust_score', ''),
            'verdict': rec.get('verdict', ''),
            'registry_source': rec.get('registry_source', ''),
            'scan_count': rec.get('scan_count', ''),
            'risk_tier': rec.get('risk', {}).get('risk_tier', '') if rec.get('risk') else '',
            'risk_rank': rec.get('risk', {}).get('risk_rank', '') if rec.get('risk') else '',
            'threat_count': rec.get('risk', {}).get('threat_count', '') if rec.get('risk') else '',
            'signal_count': len(rec.get('signals', [])),
            'attestation_count': len(rec.get('attestations', [])),
            'threat_count_detail': len(rec.get('threat_associations', [])),
            'exported_at': rec.get('exported_at', '')
        }
        writer.writerow(row)
    return output.getvalue()


def run():
    log.info('Starting %s', SERVICE_NAME)
    check_single_instance()
    try:
        log.info('%s running with PID %d', SERVICE_NAME, os.getpid())
        test_result = ws_query("SELECT 1 AS test")
        log.info('WriteService connectivity verified: %s', test_result)
    except Exception as e:
        log.error('WriteService connectivity failed: %s', e)
    remove_pid_file()


if __name__ == '__main__':
    run()