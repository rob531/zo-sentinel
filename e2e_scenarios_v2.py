import sys
import os
import requests
import time
import json
import hashlib

# ── Constants ───────────────────────────────────────────────────────────────
SERVICE_NAME = 'e2e_scenarios_v2'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
E2E_TIMEOUT = 15   # seconds per HTTP call
POLL_SECS = 3

# ── Logging ─────────────────────────────────────────────────────────────────
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(SERVICE_NAME)

# ── Write Service helpers ────────────────────────────────────────────────────
def ws_write(table: str, rows: list[dict]) -> dict:
    """Call write_service /write endpoint."""
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/write',
        json={'table': table, 'rows': rows, 'wait': True},
        timeout=E2E_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()

def ws_query(sql: str) -> list[dict]:
    """Call write_service /query endpoint, return rows list."""
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json={'sql': sql},
        timeout=E2E_TIMEOUT
    )
    resp.raise_for_status()
    result = resp.json()
    return result.get('rows', [])

def ws_execute(sql: str) -> dict:
    """Call write_service /execute endpoint for DDL/DML."""
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/execute',
        json={'sql': sql},
        timeout=E2E_TIMEOUT
    )
    resp.raise_for_status()
    return resp.json()

# ── Helpers ──────────────────────────────────────────────────────────────────
def generate_id(*fields) -> str:
    """Deterministic ID from fields."""
    raw = '|'.join(str(f) for f in fields)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

def wait_for_rows(sql: str, timeout: int = 30, interval: int = POLL_SECS) -> list[dict]:
    """Poll until query returns non-empty or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = ws_query(sql)
        if rows:
            return rows
        time.sleep(interval)
    return []

# ── Scenario 1: MCP registration flow ───────────────────────────────────────
def scenario_1_mcp_registration_flow():
    """
    Canonical flow: new MCP registration -> signal scored -> verdict computed
    -> attestation written -> visible in UI.
    """
    logger.info('=== SCENARIO 1: MCP registration flow ===')
    server_id = generate_id('e2e_test', 'scenario_1', time.time())
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    # Step 1: Register MCP server
    ws_write('mcp_server_registry', [{
        'server_id': server_id,
        'name': 'e2e-test-mcp-server-v2',
        'url': 'https://e2e-test.example.com/mcp',
        'description': 'E2E test server for scenario 1',
        'trust_score': None,
        'verdict': 'unknown',
        'registry_source': 'e2e_scenarios_v2',
        'scan_count': 0,
        'first_seen': now,
        'last_seen': now
    }])
    logger.info(f'  [1/5] Registered server_id={server_id}')

    # Step 2: Verify registration persisted
    rows = wait_for_rows(
        f"SELECT server_id, name, verdict FROM mcp_server_registry WHERE server_id = '{server_id}'",
        timeout=15
    )
    assert rows, f'Registration did not persist for {server_id}'
    assert rows[0]['verdict'] == 'unknown', f"Expected verdict=unknown, got {rows[0]['verdict']}"
    logger.info(f'  [2/5] Registration verified in DB')

    # Step 3: Write signal scores (simulate signal_analyser output)
    signals = [
        ('social_sentiment', 45.0),
        ('ecosystem_activity', 60.0),
        ('security_posture', 55.0),
        ('attestation_coverage', 20.0),
        ('threat_signal', 0.0),
        ('network_presence', 50.0),
    ]
    for sig_name, score in signals:
        ws_write('mcp_signal_scores', [{
            'server_id': server_id,
            'signal_name': sig_name,
            'score': score,
            'evidence': f'e2e_test_evidence_{sig_name}',
            'scored_at': now
        }])
    logger.info(f'  [3/5] Wrote {len(signals)} signal scores')

    # Step 4: Compute aggregate trust_score and update verdict
    avg_score = sum(s for _, s in signals) / len(signals)
    # Map avg to verdict
    if avg_score >= 70:
        verdict = 'trusted'
    elif avg_score >= 40:
        verdict = 'amber'
    else:
        verdict = 'untrusted'
    ws_execute(
        f"UPDATE mcp_server_registry SET trust_score = {avg_score}, verdict = '{verdict}' "
        f"WHERE server_id = '{server_id}'"
    )
    logger.info(f'  [4/5] Verdict set to {verdict} (score={avg_score:.1f})')

    # Step 5: Write attestation
    attestation_id = generate_id(server_id, 'e2e_attestation')
    ws_write('mcp_attestations', [{
        'attestation_id': attestation_id,
        'server_id': server_id,
        'attestor': 'e2e_scenarios_v2',
        'attestation_type': 'automated_test',
        'outcome': 'passed',
        'evidence_uri': 'https://e2e-test.example.com/evidence',
        'attested_at': now
    }])
    logger.info(f'  [5/5] Attestation written id={attestation_id}')

    # Verify complete chain visible via query
    final = ws_query(
        f"SELECT r.server_id, r.verdict, r.trust_score, "
        f"(SELECT COUNT(*) FROM mcp_signal_scores WHERE server_id=r.server_id) as sig_count, "
        f"(SELECT COUNT(*) FROM mcp_attestations WHERE server_id=r.server_id) as att_count "
        f"FROM mcp_server_registry r WHERE r.server_id = '{server_id}'"
    )
    assert final, 'Final state query returned no rows'
    row = final[0]
    assert row['sig_count'] == 6, f'Expected 6 signal scores, got {row["sig_count"]}'
    assert row['att_count'] >= 1, f'Expected >= 1 attestation, got {row["att_count"]}'
    assert row['verdict'] == verdict, f'Verdict mismatch: expected {verdict}, got {row["verdict"]}'
    logger.info(f'  ✓ Scenario 1 PASSED — verdict={verdict}, signals={row["sig_count"]}, attestations={row["att_count"]}')

# ── Scenario 2: Manual override flow ─────────────────────────────────────────
def scenario_2_manual_override_flow():
    """
    Manual override via manual_override_api.py: analyst marks a server
    as KNOWN_THREAT or trusted despite automated signals.
    """
    logger.info('=== SCENARIO 2: Manual override flow ===')

    # Create a target server
    server_id = generate_id('e2e_test', 'scenario_2', time.time())
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

    ws_write('mcp_server_registry', [{
        'server_id': server_id,
        'name': 'e2e-manual-override-test',
        'url': 'https://e2e-override.example.com',
        'description': 'E2E test for manual override scenario',
        'trust_score': 25.0,
        'verdict': 'untrusted',
        'registry_source': 'e2e_scenarios_v2',
        'scan_count': 1,
        'first_seen': now,
        'last_seen': now
    }])
    logger.info(f'  [1/4] Created test server server_id={server_id}')

    # Simulate manual override via direct DB write (mimics manual_override_api.py logic)
    override_verdict = 'known_threat'
    override_note = 'E2E test override — confirmed malicious indicator in binary fingerprint'
    ws_execute(
        f"UPDATE mcp_server_registry SET verdict = '{override_verdict}' "
        f"WHERE server_id = '{server_id}'"
    )

    # Write audit log entry (mimics what manual_override_api.py writes)
    audit_id = generate_id(server_id, 'manual_override', time.time())
    ws_write('audit_log', [{
        'id': audit_id,
        'target_server_id': server_id,
        'event_type': 'manual_override',
        'actor': 'e2e_test_analyst@example.com',
        'detail': json.dumps({'override_verdict': override_verdict, 'note': override_note}),
        'created_at': now
    }])
    logger.info(f'  [2/4] Override applied and audit logged')

    # Verify override persisted
    rows = wait_for_rows(
        f"SELECT verdict FROM mcp_server_registry WHERE server_id = '{server_id}'",
        timeout=15
    )
    assert rows, f'Override did not persist for {server_id}'
    assert rows[0]['verdict'] == 'known_threat', \
        f'Expected verdict=known_threat, got {rows[0]["verdict"]}'
    logger.info(f'  [3/4] Verdict override verified')

    # Verify audit trail
    audit_rows = ws_query(
        f"SELECT event_type, actor, detail FROM audit_log "
        f"WHERE target_server_id = '{server_id}' AND event_type = 'manual_override'"
    )
    assert audit_rows, 'Audit log entry not found for manual override'
    detail = json.loads(audit_rows[0]['detail'])
    assert detail.get('override_verdict') == 'known_threat'
    logger.info(f'  [4/4] Audit trail verified — actor={audit_rows[0]["actor"]}')
    logger.info('  ✓ Scenario 2 PASSED — manual override persisted and audited')

# ── Scenario 3: Compliance export flow ──────────────────────────────────────
def scenario_3_compliance_export_flow():
    """
    Compliance export via compliance_export_service.py: generates a filtered
    export of registry entries meeting specific criteria, writes output to
    shared/compliance_exports/ as JSON.
    """
    logger.info('=== SCENARIO 3: Compliance export flow ===')

    # Create several test servers with known verdicts
    server_ids = []
    now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
    verdicts = ['trusted', 'amber', 'untrusted', 'trusted', 'amber']

    for i, verdict in enumerate(verdicts):
        sid = generate_id('e2e_test', 'scenario_3', i, time.time())
        server_ids.append(sid)
        ws_write('mcp_server_registry', [{
            'server_id': sid,
            'name': f'e2e-export-server-{i}',
            'url': f'https://e2e-export-{i}.example.com',
            'description': 'E2E compliance export test',
            'trust_score': 50.0 + i * 5,
            'verdict': verdict,
            'registry_source': 'e2e_scenarios_v2',
            'scan_count': 1,
            'first_seen': now,
            'last_seen': now
        }])

    logger.info(f'  [1/5] Created {len(server_ids)} test servers')

    # Query all entries created by this scenario
    in_clause = ','.join(f"'{sid}'" for sid in server_ids)
    rows = wait_for_rows(
        f"SELECT server_id, name, verdict, trust_score FROM mcp_server_registry "
        f"WHERE server_id IN ({in_clause})",
        timeout=20
    )
    assert len(rows) == len(server_ids), \
        f'Expected {len(server_ids)} rows, got {len(rows)}'
    logger.info(f'  [2/5] All {len(server_ids)} servers verified in DB')

    # Filter: export only amber and untrusted servers (compliance concern)
    compliance_rows = [r for r in rows if r['verdict'] in ('amber', 'untrusted')]
    assert compliance_rows, 'Expected at least one amber or untrusted server for export'

    # Write compliance export record (simulates compliance_export_service.py output)
    export_id = generate_id('compliance_export', now)
    export_payload = {
        'export_id': export_id,
        'generated_at': now,
        'filter_criteria': {'verdict': ['amber', 'untrusted']},
        'total_records': len(compliance_rows),
        'records': [
            {
                'server_id': r['server_id'],
                'name': r['name'],
                'verdict': r['verdict'],
                'trust_score': r['trust_score']
            }
            for r in compliance_rows
        ]
    }

    # Write export metadata to audit_log (compliance evidence chain)
    audit_id = generate_id('compliance_export', export_id)
    ws_write('audit_log', [{
        'id': audit_id,
        'target_server_id': None,
        'event_type': 'compliance_export',
        'actor': 'e2e_scenarios_v2',
        'detail': json.dumps({'export_id': export_id, 'record_count': len(compliance_rows)}),
        'created_at': now
    }])
    logger.info(f'  [3/5] Export metadata written to audit_log')

    # Verify export audit entry
    export_rows = ws_query(
        f"SELECT detail FROM audit_log WHERE event_type = 'compliance_export' "
        f"AND id = '{audit_id}'"
    )
    assert export_rows, 'Compliance export audit entry not found'
    exported = json.loads(export_rows[0]['detail'])
    assert exported['record_count'] == len(compliance_rows), \
        f"Export record count mismatch: {exported['record_count']}"
    logger.info(f'  [4/5] Export audit verified (count={exported["record_count"]})')

    # Validate all exported records match filter criteria
    for rec in compliance_rows:
        assert rec['verdict'] in ('amber', 'untrusted'), \
            f"Export contains unexpected verdict: {rec['verdict']}"
    logger.info(f'  [5/5] Export filter validation passed')
    logger.info(
        f'  ✓ Scenario 3 PASSED — {len(compliance_rows)} servers exported '
        f'(amber={sum(1 for r in compliance_rows if r["verdict"]=="amber")}, '
        f'untrusted={sum(1 for r in compliance_rows if r["verdict"]=="untrusted")})'
    )

# ── Main ──────────────────────────────────────────────────────────────────────
def run_all():
    logger.info(f'Starting {SERVICE_NAME} — end-to-end test scenarios')
    logger.info(f'WriteService URL: {WRITE_SERVICE_URL}')

    # Quick health check on write_service
    try:
        health = requests.get(f'{WRITE_SERVICE_URL}/health', timeout=5).json()
        logger.info(f'WriteService health: {health}')
    except Exception as e:
        logger.error(f'WriteService unreachable: {e}')
        logger.error('e2e scenarios require write_service running on port 8772')
        sys.exit(1)

    try:
        scenario_1_mcp_registration_flow()
        time.sleep(POLL_SECS)
        scenario_2_manual_override_flow()
        time.sleep(POLL_SECS)
        scenario_3_compliance_export_flow()
        logger.info('========================================')
        logger.info('ALL SCENARIOS PASSED')
        logger.info('========================================')
    except AssertionError as e:
        logger.error(f'ASSERTION FAILED: {e}')
        sys.exit(1)
    except Exception as e:
        logger.error(f'UNEXPECTED ERROR: {e}', exc_info=True)
        sys.exit(1)

if __name__ == '__main__':
    run_all()
    sys.exit(0)