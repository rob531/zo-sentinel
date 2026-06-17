import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import requests

# ── constants ────────────────────────────────────────────────────────────────
SERVICE_NAME = 'mcp_threat_associations_writer'
PORT = None  # no HTTP port; daemon-only
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
POLL_SECS = 300  # 5-minute cycle for enrichment writes

# ── logger (no basicConfig here – called by entry point) ────────────────────
log = logging.getLogger(__name__)


# ── write_service wrappers ───────────────────────────────────────────────────
def ws_write(table: str, rows: list) -> dict:
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json={'table': table, 'rows': rows, 'wait': True},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> list:
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json={'sql': sql},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


# ── heartbeat ────────────────────────────────────────────────────────────────
def send_heartbeat(status: str = 'running') -> None:
    ts = datetime.now(timezone.utc).isoformat()
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': status,
        'ts': ts,
        'meta': {},
    }])


# ── single-instance guard ────────────────────────────────────────────────────
def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error('Another instance is already running (PID %d). Exiting.', old_pid)
            sys.exit(1)
        except OSError:
            log.warning('Stale PID file found (PID %d). Removing.', old_pid)
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame) -> None:
    log.info('Received signal %d, shutting down gracefully.', signum)
    remove_pid_file()
    sys.exit(0)


# ── core enrichment logic ────────────────────────────────────────────────────
def fetch_unscored_servers(limit: int = 100) -> list:
    """Return servers that have no threat_associations yet."""
    sql = f"""
    SELECT r.server_id, r.name, r.url, r.registry_source, r.verdict
    FROM mcp_server_registry r
    WHERE r.verdict IN ('UNKNOWN', 'AMBER', 'UNTRUSTED')
      AND NOT EXISTS (
          SELECT 1 FROM mcp_threat_associations ta
          WHERE ta.server_id = r.server_id
      )
    LIMIT {limit}
    """
    return ws_query(sql)


def resolve_threat_type(server_name: str, registry_source: str) -> tuple | None:
    """
    Derive a threat_type label from naming heuristics.
    Returns (threat_type, severity, evidence) or None.
    """
    name_lower = server_name.lower()

    indicators = {
        'KNOWN_THREAT': [],
        'HIGH_RISK_ISOLATED': [],
        'CAUTION_LIMITED': [],
        'AMBER_UNVERIFIED': [],
    }

    # keyword-based heuristics
    threat_keywords = ['exploit', 'payload', 'injection', 'backdoor', 'trojan', 'stealer', 'keylogger']
    high_risk_keywords = ['proxy', 'vpn', 'tor', 'anonymous', 'dark', 'phishing', 'fake']
    caution_keywords = ['unofficial', 'unverified', 'suspicious', 'unknown', 'test']

    for kw in threat_keywords:
        if kw in name_lower:
            indicators['KNOWN_THREAT'].append(f'keyword:{kw}')

    for kw in high_risk_keywords:
        if kw in name_lower:
            indicators['HIGH_RISK_ISOLATED'].append(f'keyword:{kw}')

    for kw in caution_keywords:
        if kw in name_lower:
            indicators['AMBER_UNVERIFIED'].append(f'keyword:{kw}')

    # registry_source heuristics
    if registry_source == 'npm':
        indicators['AMBER_UNVERIFIED'].append('source:npm_public')
    elif registry_source == 'github':
        indicators['CAUTION_LIMITED'].append('source:github_unverified')

    # pick the most severe classification with evidence
    for tier in ['KNOWN_THREAT', 'HIGH_RISK_ISOLATED', 'CAUTION_LIMITED', 'AMBER_UNVERIFIED']:
        if indicators[tier]:
            evidence = ','.join(indicators[tier])
            return (tier, tier, evidence)

    return None


def upsert_threat_association(server_id: str, threat_type: str, severity: str, evidence: str) -> None:
    """Insert or update a threat_associations row (DuckDB upsert)."""
    reported_at = datetime.now(timezone.utc).isoformat()
    sql = f"""
    INSERT INTO mcp_threat_associations (server_id, threat_type, severity, evidence, reported_at)
    VALUES ('{server_id}', '{threat_type}', '{severity}', '{evidence}', '{reported_at}')
    ON CONFLICT (server_id) DO UPDATE SET
        threat_type = EXCLUDED.threat_type,
        severity = EXCLUDED.severity,
        evidence = EXCLUDED.evidence,
        reported_at = EXCLUDED.reported_at
    """
    try:
        resp = requests.post(f'{WRITE_SERVICE_URL}/execute', json={'sql': sql}, timeout=30)
        if resp.status_code in (200, 201):
            log.debug('Upserted threat_association for server_id=%s', server_id)
        else:
            log.warning('Failed to upsert for %s: %s', server_id, resp.text)
    except Exception as exc:
        log.warning('Exception upserting threat_association for %s: %s', server_id, exc)


def cycle() -> int:
    """Process one batch of unscored servers. Returns count of rows written."""
    log.info('Starting enrichment cycle.')
    servers = fetch_unscored_servers(limit=100)
    if not servers:
        log.info('No unscored servers found.')
        return 0

    written = 0
    for srv in servers:
        server_id = srv.get('server_id') or srv.get('server_id')
        name = srv.get('name', 'unknown')
        registry_source = srv.get('registry_source', 'unknown')
        verdict = srv.get('verdict', 'UNKNOWN')

        result = resolve_threat_type(name, registry_source)
        if result:
            threat_type, severity, evidence = result
            upsert_threat_association(server_id, threat_type, severity, evidence)
            written += 1
            log.info('Processed server_id=%s verdict=%s -> %s', server_id, verdict, threat_type)
        else:
            # No threat indicators – write a benign entry to close the gap
            upsert_threat_association(
                server_id,
                threat_type='TRUSTED_RESEARCH',
                severity='AMBER_UNVERIFIED',
                evidence='no_indicators_found;verdict_based_on_absence',
            )
            written += 1
            log.info('Processed server_id=%s verdict=%s -> TRUSTED_RESEARCH (benign fill)', server_id, verdict)

    log.info('Enrichment cycle complete. Rows written: %d', written)
    return written


# ── daemon entry ─────────────────────────────────────────────────────────────
def run() -> None:
    log.info('Starting %s daemon.', SERVICE_NAME)
    check_single_instance()

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    send_heartbeat(status='starting')

    while True:
        try:
            send_heartbeat(status='running')
            cycle()
        except Exception as exc:
            log.exception('Error in cycle: %s', exc)
            send_heartbeat(status='error')

        time.sleep(POLL_SECS)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[
            logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
            logging.StreamHandler(),
        ],
    )
    run()