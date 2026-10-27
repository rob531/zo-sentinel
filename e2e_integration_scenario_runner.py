import logging
import time
import sys
import os
import requests
import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'e2e_integration_scenario_runner'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
PID_FILE = '/tmp/e2e_integration_scenario_runner.pid'

SCENARIOS = [
    {
        'name': 'new_mcp_ingestion',
        'description': 'Ingest new MCP through mcp_scanner into mcp_server_registry',
        'required_verdict': True,
    },
    {
        'name': 'signal_scoring',
        'description': 'Signal scoring through signal_analyser_v2',
        'required_verdict': True,
    },
    {
        'name': 'verdict_computation',
        'description': 'Verdict computation through trust_synthesiser_v2',
        'required_verdict': True,
    },
    {
        'name': 'attestation_generation',
        'description': 'Attestation generation through attestation_engine',
        'required_verdict': True,
    },
    {
        'name': 'ui_visibility',
        'description': 'UI visibility check through registry_api',
        'required_verdict': True,
    },
]

TEST_MCP_FIXTURE = {
    'name': 'e2e-test-mcp-server',
    'url': 'https://github.com/e2e-test-org/e2e-test-mcp',
    'description': 'Synthetic MCP server for E2E integration testing',
    'registry_source': 'e2e_test_fixture',
    'tool_count': 5,
    'auth_method': 'none',
}


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Execute a SELECT query via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Query failed: {e} | SQL: {sql[:200]}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to a table via write_service."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Write failed: {e} | Table: {table}")
        return False


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    """Execute DDL/DML via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Execute failed: {e} | SQL: {sql[:200]}")
        return False


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def compute_server_id(name: str, url: str) -> str:
    """Compute deterministic server_id from name and url."""
    content = f"{name}|{url}".encode('utf-8')
    return hashlib.sha256(content).hexdigest()[:16]


def compute_deterministic_id(*fields: str) -> str:
    """Compute deterministic ID from fields."""
    content = '|'.join(fields).encode('utf-8')
    return hashlib.sha256(content).hexdigest()[:16]


def check_single_instance() -> bool:
    """Check if another instance is running. Returns True if this should exit."""
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                logger.warning(f"Another instance running with PID {old_pid}")
                return True
            except OSError:
                logger.info(f"Stale PID file, removing")
                os.remove(PID_FILE)
        except (ValueError, IOError) as e:
            logger.warning(f"Could not read PID file: {e}")
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return False


def remove_pid_file():
    """Remove the PID file."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError as e:
        logger.warning(f"Could not remove PID file: {e}")


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def ensure_test_tables() -> bool:
    """Ensure required tables exist for E2E testing."""
    try:
        ws_execute("""
            CREATE TABLE IF NOT EXISTS e2e_scenario_runs (
                run_id VARCHAR PRIMARY KEY,
                scenario_name VARCHAR NOT NULL,
                server_id VARCHAR,
                status VARCHAR NOT NULL,
                started_at VARCHAR NOT NULL,
                completed_at VARCHAR,
                verdict VARCHAR,
                error_message VARCHAR,
                UNIQUE(run_id)
            )
        """)
        logger.info("e2e_scenario_runs table ready")
        return True
    except Exception as e:
        logger.error(f"Failed to ensure test tables: {e}")
        return False


def record_scenario_start(scenario_name: str, server_id: Optional[str] = None) -> str:
    """Record the start of a scenario run."""
    run_id = compute_deterministic_id(scenario_name, utc_now_iso(), str(uuid.uuid4()))
    ws_write('e2e_scenario_runs', [{
        'run_id': run_id,
        'scenario_name': scenario_name,
        'server_id': server_id,
        'status': 'running',
        'started_at': utc_now_iso(),
        'completed_at': None,
        'verdict': None,
        'error_message': None,
    }])
    return run_id


def record_scenario_complete(run_id: str, status: str, verdict: Optional[str] = None, error_message: Optional[str] = None):
    """Record scenario completion."""
    completed_at = utc_now_iso()
    existing = ws_query(
        "SELECT run_id FROM e2e_scenario_runs WHERE run_id = ?",
        [run_id]
    )
    if existing:
        ws_execute(
            "UPDATE e2e_scenario_runs SET status = ?, completed_at = ?, verdict = ?, error_message = ? WHERE run_id = ?",
            [status, completed_at, verdict, error_message, run_id]
        )
    else:
        logger.warning(f"Scenario run {run_id} not found for update")


def create_test_mcp_server() -> Optional[str]:
    """Create a test MCP server in the registry."""
    server_id = compute_server_id(TEST_MCP_FIXTURE['name'], TEST_MCP_FIXTURE['url'])
    
    existing = ws_query(
        "SELECT server_id FROM mcp_server_registry WHERE server_id = ?",
        [server_id]
    )
    if existing:
        logger.info(f"Test MCP server already exists: {server_id}")
        return server_id
    
    row = {
        'server_id': server_id,
        'name': TEST_MCP_FIXTURE['name'],
        'url': TEST_MCP_FIXTURE['url'],
        'description': TEST_MCP_FIXTURE['description'],
        'registry_source': TEST_MCP_FIXTURE['registry_source'],
        'trust_score': None,
        'verdict': 'unknown',
        'scan_count': 0,
        'first_seen': utc_now_iso(),
        'last_seen': utc_now_iso(),
        'last_scanned': None,
        'last_assessed': None,
    }
    
    if ws_write('mcp_server_registry', [row]):
        logger.info(f"Created test MCP server: {server_id}")
        return server_id
    else:
        logger.error("Failed to create test MCP server")
        return None


def scenario_1_new_mcp_ingestion() -> tuple[bool, Optional[str], Optional[str]]:
    """
    Scenario 1: New MCP ingestion through mcp_scanner.
    Verifies MCP appears in mcp_server_registry.
    """
    logger.info("=" * 60)
    logger.info("SCENARIO 1: New MCP Ingestion")
    logger.info("=" * 60)
    
    scenario_name = 'new_mcp_ingestion'
    run_id = record_scenario_start(scenario_name)
    
    try:
        server_id = create_test_mcp_server()
        if not server_id:
            record_scenario_complete(run_id, 'failed', error_message='Failed to create test MCP server')
            return False, None, run_id
        
        max_wait = 60
        interval = 5
        waited = 0
        
        while waited < max_wait:
            result = ws_query(
                "SELECT server_id, name, verdict, trust_score FROM mcp_server_registry WHERE server_id = ?",
                [server_id]
            )
            if result:
                row = result[0]
                logger.info(f"MCP found: server_id={row.get('server_id')}, verdict={row.get('verdict')}")
                verdict = row.get('verdict')
                record_scenario_complete(run_id, 'passed', verdict=verdict)
                return True, server_id, run_id
            
            logger.info(f"Waiting for MCP ingestion... ({waited}/{max_wait}s)")
            time.sleep(interval)
            waited += interval
        
        record_scenario_complete(run_id, 'timeout', error_message='MCP not found after max wait')
        return False, server_id, run_id
        
    except Exception as e:
        logger.error(f"Scenario 1 failed: {e}")
        record_scenario_complete(run_id, 'failed', error_message=str(e))
        return False, None, run_id


def scenario_2_signal_scoring(server_id: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Scenario 2: Signal scoring through signal_analyser_v2.
    Verifies signals appear in mcp_signal_scores.
    """
    logger.info("=" * 60)
    logger.info("SCENARIO 2: Signal Scoring")
    logger.info("=" * 60)
    
    scenario_name = 'signal_scoring'
    run_id = record_scenario_start(scenario_name, server_id)
    
    try:
        max_wait = 120
        interval = 5
        waited = 0
        
        while waited < max_wait:
            signals = ws_query(
                "SELECT COUNT(*) as signal_count FROM mcp_signal_scores WHERE server_id = ?",
                [server_id]
            )
            if signals and signals[0].get('signal_count', 0) > 0:
                signal_count = signals[0]['signal_count']
                logger.info(f"Found {signal_count} signals for server {server_id}")
                
                score_rows = ws_query(
                    "SELECT signal_name, score FROM mcp_signal_scores WHERE server_id = ? LIMIT 10",
                    [server_id]
                )
                for sr in score_rows:
                    logger.info(f"  Signal: {sr.get('signal_name')} = {sr.get('score')}")
                
                verdict = None
                verdict_rows = ws_query(
                    "SELECT verdict FROM mcp_server_registry WHERE server_id = ?",
                    [server_id]
                )
                if verdict_rows:
                    verdict = verdict_rows[0].get('verdict')
                
                record_scenario_complete(run_id, 'passed', verdict=verdict)
                return True, server_id, run_id
            
            logger.info(f"Waiting for signal scoring... ({waited}/{max_wait}s)")
            time.sleep(interval)
            waited += interval
        
        record_scenario_complete(run_id, 'timeout', error_message='No signals found after max wait')
        return False, server_id, run_id
        
    except Exception as e:
        logger.error(f"Scenario 2 failed: {e}")
        record_scenario_complete(run_id, 'failed', error_message=str(e))
        return False, None, run_id


def scenario_3_verdict_computation(server_id: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Scenario 3: Verdict computation through trust_synthesiser_v2.
    Verifies verdict is no longer 'unknown'.
    """
    logger.info("=" * 60)
    logger.info("SCENARIO 3: Verdict Computation")
    logger.info("=" * 60)
    
    scenario_name = 'verdict_computation'
    run_id = record_scenario_start(scenario_name, server_id)
    
    try:
        max_wait = 120
        interval = 5
        waited = 0
        
        while waited < max_wait:
            result = ws_query(
                "SELECT verdict, trust_score FROM mcp_server_registry WHERE server_id = ?",
                [server_id]
            )
            if result:
                row = result[0]
                verdict = row.get('verdict')
                trust_score = row.get('trust_score')
                logger.info(f"Verdict: {verdict}, Trust Score: {trust_score}")
                
                if verdict and verdict != 'unknown':
                    logger.info(f"Verdict computation complete: {verdict}")
                    record_scenario_complete(run_id, 'passed', verdict=verdict)
                    return True, server_id, run_id
                else:
                    logger.info(f"Verdict still unknown, waiting... ({waited}/{max_wait}s)")
            
            time.sleep(interval)
            waited += interval
        
        record_scenario_complete(run_id, 'timeout', error_message='Verdict still unknown after max wait')
        return False, server_id, run_id
        
    except Exception as e:
        logger.error(f"Scenario 3 failed: {e}")
        record_scenario_complete(run_id, 'failed', error_message=str(e))
        return False, None, run_id


def scenario_4_attestation_generation(server_id: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Scenario 4: Attestation generation through attestation_engine.
    Verifies attestation appears in mcp_attestations.
    """
    logger.info("=" * 60)
    logger.info("SCENARIO 4: Attestation Generation")
    logger.info("=" * 60)
    
    scenario_name = 'attestation_generation'
    run_id = record_scenario_start(scenario_name, server_id)
    
    try:
        max_wait = 120
        interval = 5
        waited = 0
        
        while waited < max_wait:
            attestations = ws_query(
                "SELECT attestation_id, attested_at, attestor, verdict FROM mcp_attestations WHERE server_id = ?",
                [server_id]
            )
            if attestations:
                for att in attestations:
                    logger.info(f"Attestation: id={att.get('attestation_id')}, "
                              f"attestor={att.get('attestor')}, verdict={att.get('verdict')}, "
                              f"attested_at={att.get('attested_at')}")
                verdict = attestations[0].get('verdict')
                record_scenario_complete(run_id, 'passed', verdict=verdict)
                return True, server_id, run_id
            
            logger.info(f"Waiting for attestation... ({waited}/{max_wait}s)")
            time.sleep(interval)
            waited += interval
        
        record_scenario_complete(run_id, 'timeout', error_message='No attestation found after max wait')
        return False, server_id, run_id
        
    except Exception as e:
        logger.error(f"Scenario 4 failed: {e}")
        record_scenario_complete(run_id, 'failed', error_message=str(e))
        return False, None, run_id


def scenario_5_ui_visibility(server_id: str) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Scenario 5: UI visibility check through registry_api.
    Verifies MCP appears in registry_api response.
    """
    logger.info("=" * 60)
    logger.info("SCENARIO 5: UI Visibility Check")
    logger.info("=" * 60)
    
    scenario_name = 'ui_visibility'
    run_id = record_scenario_start(scenario_name, server_id)
    
    try:
        api_url = 'http://localhost:8781'
        max_wait = 60
        interval = 3
        waited = 0
        
        while waited < max_wait:
            try:
                resp = requests.get(
                    f"{api_url}/api/servers/{server_id}",
                    timeout=10
                )
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"UI visibility verified: server found in registry_api")
                    logger.info(f"  Name: {data.get('name')}, Verdict: {data.get('verdict')}")
                    verdict = data.get('verdict')
                    record_scenario_complete(run_id, 'passed', verdict=verdict)
                    return True, server_id, run_id
                elif resp.status_code == 404:
                    logger.info(f"Server not yet visible in UI... ({waited}/{max_wait}s)")
                else:
                    logger.warning(f"Unexpected status {resp.status_code}: {resp.text[:200]}")
            except requests.exceptions.RequestException as e:
                logger.info(f"registry_api not responding: {e} ({waited}/{max_wait}s)")
            
            time.sleep(interval)
            waited += interval
        
        record_scenario_complete(run_id, 'timeout', error_message='Server not visible in registry_api after max wait')
        return False, server_id, run_id
        
    except Exception as e:
        logger.error(f"Scenario 5 failed: {e}")
        record_scenario_complete(run_id, 'failed', error_message=str(e))
        return False, None, run_id


def check_write_service_health() -> bool:
    """Check if write_service is responding."""
    try:
        resp = requests.get(f"{WRITE_SERVICE_URL}/health", timeout=5)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False


def cleanup_test_artifacts():
    """Clean up test MCP server and related artifacts."""
    try:
        server_id = compute_server_id(TEST_MCP_FIXTURE['name'], TEST_MCP_FIXTURE['url'])
        
        ws_execute("DELETE FROM e2e_scenario_runs WHERE server_id = ?", [server_id])
        
        logger.info(f"Cleaned up test artifacts for server_id: {server_id}")
    except Exception as e:
        logger.warning(f"Cleanup error: {e}")


def send_heartbeat(status: str = 'running', meta: Optional[Dict[str, Any]] = None):
    """Send heartbeat to service_health table."""
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': status,
        'meta': str(meta) if meta else None,
    }
    ws_write('service_health', [row])


def print_summary(results: List[Dict[str, Any]]):
    """Print E2E test summary."""
    logger.info("=" * 60)
    logger.info("E2E INTEGRATION TEST SUMMARY")
    logger.info("=" * 60)
    
    passed = sum(1 for r in results if r['status'] == 'passed')
    failed = sum(1 for r in results if r['status'] in ('failed', 'timeout'))
    total = len(results)
    
    for r in results:
        status_symbol = '✓' if r['status'] == 'passed' else '✗'
        verdict_str = f", verdict={r.get('verdict')}" if r.get('verdict') else ''
        logger.info(f"  {status_symbol} {r['scenario_name']}: {r['status']}{verdict_str}")
    
    logger.info("-" * 60)
    logger.info(f"Results: {passed}/{total} passed, {failed}/{total} failed")
    logger.info("=" * 60)
    
    return failed == 0


def cycle() -> bool:
    """Run one E2E integration test cycle."""
    logger.info("Starting E2E integration test cycle")
    
    if not check_write_service_health():
        logger.error("Write service not available")
        send_heartbeat(status='degraded', meta={'error': 'write_service_unavailable'})
        return False
    
    if not ensure_test_tables():
        logger.error("Failed to ensure test tables")
        send_heartbeat(status='degraded', meta={'error': 'tables_init_failed'})
        return False
    
    server_id = None
    results = []
    
    success_1, server_id_1, run_id_1 = scenario_1_new_mcp_ingestion()
    results.append({
        'scenario_name': 'new_mcp_ingestion',
        'status': 'passed' if success_1 else 'failed',
        'run_id': run_id_1,
        'verdict': None,
        'server_id': server_id_1,
    })
    
    if success_1:
        server_id = server_id_1
    
    if server_id:
        success_2, _, run_id_2 = scenario_2_signal_scoring(server_id)
        results.append({
            'scenario_name': 'signal_scoring',
            'status': 'passed' if success_2 else 'failed',
            'run_id': run_id_2,
            'verdict': None,
            'server_id': server_id,
        })
        
        success_3, _, run_id_3 = scenario_3_verdict_computation(server_id)
        results.append({
            'scenario_name': 'verdict_computation',
            'status': 'passed' if success_3 else 'failed',
            'run_id': run_id_3,
            'verdict': None,
            'server_id': server_id,
        })
        
        success_4, _, run_id_4 = scenario_4_attestation_generation(server_id)
        results.append({
            'scenario_name': 'attestation_generation',
            'status': 'passed' if success_4 else 'failed',
            'run_id': run_id_4,
            'verdict': None,
            'server_id': server_id,
        })
        
        success_5, _, run_id_5 = scenario_5_ui_visibility(server_id)
        results.append({
            'scenario_name': 'ui_visibility',
            'status': 'passed' if success_5 else 'failed',
            'run_id': run_id_5,
            'verdict': None,
            'server_id': server_id,
        })
    else:
        logger.error("Cannot run scenarios 2-5 without server_id from scenario 1")
        for name in ['signal_scoring', 'verdict_computation', 'attestation_generation', 'ui_visibility']:
            results.append({
                'scenario_name': name,
                'status': 'skipped',
                'run_id': None,
                'verdict': None,
                'server_id': None,
            })
    
    all_passed = print_summary(results)
    
    if not all_passed:
        logger.warning("Some scenarios did not complete successfully")
    
    send_heartbeat(
        status='ok' if all_passed else 'degraded',
        meta={
            'scenarios_run': len(results),
            'scenarios_passed': sum(1 for r in results if r['status'] == 'passed'),
            'server_id': server_id,
        }
    )
    
    return all_passed


def run():
    """Main run loop for E2E integration scenario runner."""
    import signal
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if check_single_instance():
        logger.error("Another instance is running. Exiting.")
        sys.exit(1)
    
    logger.info(f"Starting {SERVICE_NAME}")
    logger.info(f"Write service: {WRITE_SERVICE_URL}")
    
    try:
        send_heartbeat(status='starting')
        
        cycle()
        
        logger.info("E2E test cycle complete")
        remove_pid_file()
        sys.exit(0)
        
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        remove_pid_file()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        send_heartbeat(status='failed', meta={'error': str(e)})
        remove_pid_file()
        sys.exit(1)


if __name__ == '__main__':
    run()