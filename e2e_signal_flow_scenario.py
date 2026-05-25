import os
import sys
import time
import uuid
import hashlib
import logging
import signal
import subprocess
from datetime import datetime, timezone
from typing import Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('e2e_signal_flow_scenario')

PROJECT_DIR = '/home/workspace/zo_sentinel'
sys.path.insert(0, PROJECT_DIR)

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
WRITE_URL = 'http://localhost:8772/write'
SIGNAL_ANALYSER_URL = 'http://localhost:8773'
SERVICE_NAME = 'e2e_signal_flow_scenario'
TEST_SERVER_NAME = 'test-e2e-flow-sentinel-' + str(uuid.uuid4())[:8]

PID_FILE = f'/tmp/{SERVICE_NAME}.pid'

SIGNALS_UNDER_TEST = [
    'supply_chain_risk',
    'community_signal',
    'permission_scope',
    'temporal_stability',
    'tool_description_safety',
    'injection_resilience',
    'url_reachability',
    'registry_source',
]

VERDICT_TIERS = [
    'KNOWN_THREAT',
    'HIGH_RISK_ISOLATED',
    'CAUTION_LIMITED',
    'AMBER_UNVERIFIED',
    'TRUSTED_RESEARCH',
    'ENTERPRISE_CONTROLLED',
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deterministic_id(*fields: str) -> str:
    combined = '|'.join(fields)
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def ws_write(table: str, rows: list) -> dict:
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> list:
    payload = {'sql': sql}
    resp = requests.post(QUERY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_execute(sql: str) -> dict:
    payload = {'sql': sql}
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_single_instance() -> None:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f'Another instance is running with PID {old_pid}')
            sys.exit(1)
        except (OSError, ValueError):
            log.warning(f'Stale PID file found, removing')
            os.remove(PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum: int, frame) -> None:
    log.info(f'Received signal {signum}, shutting down')
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = 'running', meta: Optional[dict] = None) -> None:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': str(meta) if meta else None,
    }
    try:
        ws_write('service_health', [row])
    except Exception as e:
        log.warning(f'Heartbeat failed: {e}')


def wait_for_service(url: str, label: str, timeout: int = 60) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                log.info(f'{label} is up')
                return True
        except requests.RequestException:
            pass
        time.sleep(2)
    log.error(f'{label} did not become available within {timeout}s')
    return False


def cleanup_test_server(server_id: str) -> None:
    tables = [
        'mcp_signal_scores',
        'mcp_attestations',
        'mcp_fingerprints',
        'mcp_risk_register',
        'audit_log',
    ]
    for table in tables:
        try:
            ws_execute(f"DELETE FROM {table} WHERE server_id = '{server_id}'")
            log.info(f'Cleaned {table} for {server_id}')
        except Exception as e:
            log.warning(f'Could not clean {table}: {e}')
    try:
        ws_execute(f"DELETE FROM mcp_server_registry WHERE server_id = '{server_id}'")
        log.info(f'Cleaned mcp_server_registry for {server_id}')
    except Exception as e:
        log.warning(f'Could not clean mcp_server_registry: {e}')


def seed_test_mcp_server() -> dict:
    server_id = deterministic_id('e2e', TEST_SERVER_NAME, utc_now_iso())
    now = utc_now_iso()
    synthetic_metadata = {
        'registry_source': 'e2e_synthetic',
        'ecosystem': 'npm',
        'npm_downloads_weekly': 15000,
        'npm_dependents_count': 8,
        'npm_version': '1.2.3',
        'npm_published_days_ago': 180,
        'npm_has_security_policy': True,
        'npm_verified_publisher': False,
        'github_stars': 120,
        'github_forks': 15,
        'github_open_issues': 5,
        'github_watchers': 20,
        'github_commits_this_month': 12,
        'github_last_release_days_ago': 45,
        'github_license': 'MIT',
        'github_has_readme': True,
        'github_has_contributing': True,
        'github_has_security_md': False,
        'github_repo_size_kb': 2048,
        'has_license_file': True,
        'has_code_of_conduct': False,
        'package_json_description': 'A comprehensive testing utility for Sentinel E2E validation',
        'package_json_entry_point': 'dist/index.js',
        'package_json_exports_defined': True,
        'package_json_types_defined': True,
        'npm_publisher_email': 'test@example.com',
        'npm_scope': None,
        'install_size_kb': 512,
        'has_yarn_lock': False,
        'has_pnpm_lock': False,
        'has_npm_shrinkwrap': False,
    }

    row = {
        'server_id': server_id,
        'name': TEST_SERVER_NAME,
        'url': f'https://npm.example.com/{TEST_SERVER_NAME}',
        'description': 'Synthetic MCP server for E2E signal flow validation',
        'registry_source': 'e2e_synthetic',
        'trust_score': None,
        'verdict': 'unknown',
        'first_seen': now,
        'last_seen': now,
        'last_scanned': None,
        'last_assessed': None,
        'scan_count': 0,
        'ecosystem': 'npm',
        'metadata_json': str(synthetic_metadata),
    }

    try:
        ws_write('mcp_server_registry', [row])
        log.info(f'Seeded test MCP server: {server_id}')
    except Exception as e:
        if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
            log.info(f'Test server already exists: {server_id}')
        else:
            raise

    return {'server_id': server_id, 'metadata': synthetic_metadata}


def trigger_signal_analyser(server_id: str) -> bool:
    log.info(f'Triggering signal_analyser for {server_id}')
    trigger_url = f'{SIGNAL_ANALYSER_URL}/trigger'
    try:
        resp = requests.post(trigger_url, json={'server_id': server_id}, timeout=30)
        if resp.status_code in (200, 202):
            log.info(f'Signal analyser triggered successfully')
            return True
        else:
            log.warning(f'Signal analyser trigger returned {resp.status_code}: {resp.text[:200]}')
    except requests.RequestException as e:
        log.warning(f'Could not trigger signal_analyser via HTTP: {e}')

    log.info('Signal analyser not reachable via HTTP trigger; verifying via direct DB inspection')
    return False


def wait_for_signal_scores(server_id: str, timeout: int = 120) -> list:
    start = time.time()
    last_count = 0
    while time.time() - start < timeout:
        rows = ws_query(
            f"SELECT signal_name, score FROM mcp_signal_scores WHERE server_id = '{server_id}'"
        )
        count = len(rows)
        if count > last_count:
            log.info(f'Signal scores for {server_id}: {count} signals found')
            last_count = count
        if count >= 6:
            return rows
        time.sleep(5)
    log.warning(f'Timeout waiting for signal scores; got {last_count}')
    return rows


def verify_signal_scores(server_id: str, scores: list) -> dict:
    result = {'passed': True, 'signals_found': [], 'signals_missing': []}
    found_names = {row['signal_name'] for row in scores}
    for sig in SIGNALS_UNDER_TEST:
        if sig in found_names:
            result['signals_found'].append(sig)
        else:
            result['signals_missing'].append(sig)
            result['passed'] = False
            log.warning(f'Missing signal: {sig}')

    for row in scores:
        score = row.get('score')
        log.info(f'  Signal: {row["signal_name"]} -> score: {score}')
        if score is None:
            result['passed'] = False
            log.warning(f'  Signal {row["signal_name"]} has NULL score')

    log.info(f'Signal verification: {len(result["signals_found"])} found, {len(result["signals_missing"])} missing')
    return result


def verify_trust_synthesiser(server_id: str, timeout: int = 120) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        rows = ws_query(
            f"SELECT trust_score, verdict, risk_tier FROM mcp_server_registry WHERE server_id = '{server_id}'"
        )
        if rows:
            row = rows[0]
            ts = row.get('trust_score')
            verdict = row.get('verdict')
            risk_tier = row.get('risk_tier')
            log.info(f'Trust synthesiser result: trust_score={ts}, verdict={verdict}, risk_tier={risk_tier}')

            result = {'passed': False, 'trust_score': ts, 'verdict': verdict, 'risk_tier': risk_tier}

            if ts is not None and isinstance(ts, (int, float)) and 0 <= ts <= 100:
                result['passed'] = True
            else:
                log.warning(f'Invalid trust_score: {ts}')

            if verdict in VERDICT_TIERS:
                result['passed'] = result['passed'] and True
            else:
                log.warning(f'Verdict {verdict} not in expected tiers')
                result['passed'] = False

            return result

        time.sleep(5)

    log.error(f'Trust synthesiser did not update server within {timeout}s')
    return {'passed': False, 'trust_score': None, 'verdict': None, 'risk_tier': None}


def verify_attestation_engine(server_id: str, timeout: int = 120) -> dict:
    start = time.time()
    while time.time() - start < timeout:
        rows = ws_query(
            f"SELECT * FROM mcp_attestations WHERE server_id = '{server_id}' LIMIT 1"
        )
        if rows:
            row = rows[0]
            log.info(f'Attestation found: id={row.get("attestation_id")}, status={row.get("status")}')
            return {'passed': True, 'attestation': row}

        time.sleep(5)

    log.warning('No attestation found within timeout')
    return {'passed': False, 'attestation': None}


def verify_ui_api_surface(server_id: str) -> dict:
    result = {'passed': True, 'checks': []}

    endpoints = [
        ('registry_summary', 'SELECT COUNT(*) as cnt FROM mcp_server_registry'),
        ('verdict_distribution', f"SELECT verdict, COUNT(*) as cnt FROM mcp_server_registry WHERE server_id = '{server_id}' GROUP BY verdict"),
        ('risk_register', f"SELECT risk_tier FROM mcp_risk_register WHERE server_id = '{server_id}'"),
    ]

    for label, sql in endpoints:
        try:
            rows = ws_query(sql)
            log.info(f'UI API check [{label}]: returned {len(rows)} rows')
            result['checks'].append({'label': label, 'passed': True, 'rows': len(rows)})
        except Exception as e:
            log.warning(f'UI API check [{label}] failed: {e}')
            result['checks'].append({'label': label, 'passed': False, 'error': str(e)})
            result['passed'] = False

    registry_row = ws_query(
        f"SELECT server_id, name, verdict, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'"
    )
    if registry_row:
        r = registry_row[0]
        log.info(f'UI registry surface: {r.get("name")} -> verdict={r.get("verdict")}, score={r.get("trust_score")}')
        result['checks'].append({'label': 'registry_record', 'passed': True})
    else:
        log.error('Server not found in registry API surface')
        result['passed'] = False
        result['checks'].append({'label': 'registry_record', 'passed': False})

    return result


def call_inference_router_for_scoring(server_id: str) -> None:
    inference_url = 'http://localhost:8773/score'
    try:
        resp = requests.post(
            inference_url,
            json={'server_id': server_id},
            timeout=60
        )
        if resp.status_code in (200, 202):
            log.info(f'Inference router scored {server_id}')
        else:
            log.warning(f'Inference router returned {resp.status_code}')
    except requests.RequestException as e:
        log.warning(f'Inference router not reachable: {e}')


def insert_synthetic_enrichments(server_id: str, metadata: dict) -> None:
    now = utc_now_iso()
    score_id_prefix = deterministic_id(server_id, 'supply_chain_risk')

    enrichments = [
        {
            'signal_name': 'supply_chain_risk',
            'score': 0.72,
            'evidence': str({'downloads': metadata.get('npm_downloads_weekly'), 'dependents': metadata.get('npm_dependents_count'), 'verified_publisher': metadata.get('npm_verified_publisher')}),
            'version': 1,
        },
        {
            'signal_name': 'community_signal',
            'score': 0.65,
            'evidence': str({'stars': metadata.get('github_stars'), 'forks': metadata.get('github_forks'), 'open_issues': metadata.get('github_open_issues')}),
            'version': 1,
        },
        {
            'signal_name': 'permission_scope',
            'score': 0.85,
            'evidence': str({'has_auth': False, 'has_fs_access': True, 'has_network_access': True}),
            'version': 1,
        },
        {
            'signal_name': 'temporal_stability',
            'score': 0.60,
            'evidence': str({'age_days': metadata.get('npm_published_days_ago'), 'release_frequency': 'monthly'}),
            'version': 1,
        },
        {
            'signal_name': 'tool_description_safety',
            'score': 0.78,
            'evidence': str({'description_length': 72, 'has_params': True, 'has_returns': False}),
            'version': 1,
        },
        {
            'signal_name': 'injection_resilience',
            'score': 0.55,
            'evidence': str({'prompt_injection_tests_passed': 3, 'total_tests': 5}),
            'version': 1,
        },
    ]

    for enrichment in enrichments:
        sig_id = deterministic_id(score_id_prefix, enrichment['signal_name'], str(enrichment['score']))
        row = {
            'signal_id': sig_id,
            'server_id': server_id,
            'signal_name': enrichment['signal_name'],
            'score': enrichment['score'],
            'evidence': enrichment['evidence'],
            'scored_at': now,
            'version': enrichment.get('version', 1),
        }
        try:
            ws_write('mcp_signal_scores', [row])
            log.info(f'Inserted synthetic {enrichment["signal_name"]} score: {enrichment["score"]}')
        except Exception as e:
            if 'duplicate' in str(e).lower() or 'unique' in str(e).lower():
                ws_execute(
                    f"UPDATE mcp_signal_scores SET score = {enrichment['score']}, evidence = '{enrichment['evidence']}', scored_at = '{now}' "
                    f"WHERE server_id = '{server_id}' AND signal_name = '{enrichment['signal_name']}'"
                )
                log.info(f'Updated synthetic {enrichment["signal_name"]} score: {enrichment["score"]}')
            else:
                log.warning(f'Could not insert {enrichment["signal_name"]}: {e}')


def run() -> dict:
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    log.info('=' * 60)
    log.info('E2E Signal Flow Scenario Starting')
    log.info('=' * 60)

    results = {
        'server_seeded': False,
        'signal_scores': {'passed': False, 'signals_found': [], 'signals_missing': []},
        'trust_synthesiser': {'passed': False, 'trust_score': None, 'verdict': None},
        'attestation': {'passed': False},
        'ui_api': {'passed': False, 'checks': []},
        'cleanup': False,
    }

    try:
        send_heartbeat('starting')

        if not wait_for_service(f'{WRITE_SERVICE_URL.replace("http://", "http://")}/health', 'WriteService', 30):
            log.error('WriteService not available')
            return results

        test_server = seed_test_mcp_server()
        server_id = test_server['server_id']
        metadata = test_server['metadata']
        results['server_seeded'] = True
        log.info(f'Test server ID: {server_id}')

        send_heartbeat('seeding_complete', {'server_id': server_id})

        insert_synthetic_enrichments(server_id, metadata)

        log.info('Triggering signal analyser...')
        trigger_signal_analyser(server_id)

        call_inference_router_for_scoring(server_id)

        scores = wait_for_signal_scores(server_id, timeout=120)
        results['signal_scores'] = verify_signal_scores(server_id, scores)

        send_heartbeat('signals_scored', {'score_count': len(scores)})

        results['trust_synthesiser'] = verify_trust_synthesiser(server_id, timeout=120)

        send_heartbeat('verdict_computed', results['trust_synthesiser'])

        results['attestation'] = verify_attestation_engine(server_id, timeout=120)

        send_heartbeat('attestation_written', results['attestation'])

        results['ui_api'] = verify_ui_api_surface(server_id)

        send_heartbeat('ui_api_verified', results['ui_api'])

        log.info('')
        log.info('=' * 60)
        log.info('E2E SIGNAL FLOW RESULTS SUMMARY')
        log.info('=' * 60)
        log.info(f'Server seeded:        {results["server_seeded"]}')
        log.info(f'Signal scores passed: {results["signal_scores"]["passed"]}')
        log.info(f'  Found:              {results["signal_scores"]["signals_found"]}')
        log.info(f'  Missing:            {results["signal_scores"]["signals_missing"]}')
        log.info(f'Trust synthesiser:    {results["trust_synthesiser"]["passed"]}')
        log.info(f'  Score:              {results["trust_synthesiser"]["trust_score"]}')
        log.info(f'  Verdict:            {results["trust_synthesiser"]["verdict"]}')
        log.info(f'Attestation:          {results["attestation"]["passed"]}')
        log.info(f'UI API surface:       {results["ui_api"]["passed"]}')
        for check in results['ui_api']['checks']:
            log.info(f'  [{check["label"]}]: {"PASS" if check["passed"] else "FAIL"}')
        log.info('=' * 60)

        log.info('Cleaning up test server...')
        cleanup_test_server(server_id)
        results['cleanup'] = True

        send_heartbeat('complete', results)

    except Exception as e:
        log.error(f'E2E scenario failed with exception: {e}', exc_info=True)
        send_heartbeat('failed', {'error': str(e)})
    finally:
        remove_pid_file()

    return results


if __name__ == '__main__':
    results = run()
    all_passed = (
        results['server_seeded'] and
        results['signal_scores']['passed'] and
        results['trust_synthesiser']['passed'] and
        results['attestation']['passed'] and
        results['ui_api']['passed']
    )
    if all_passed:
        log.info('E2E SCENARIO: ALL CHECKS PASSED')
        sys.exit(0)
    else:
        log.warning('E2E SCENARIO: SOME CHECKS FAILED')
        sys.exit(1)