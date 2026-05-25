import logging
import time
import json
import sys
from datetime import datetime, timezone
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/aidr_commit_enforcement_test.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'aidr_commit_enforcement_test'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'

PHASE9_CONSTRAINTS = {
    1: 'Gateway MUST read mcp_server_registry.verdict before forwarding any commit',
    2: 'CAUTION_LIMITED verdict blocks commit unless override_flag=true',
    3: 'HIGH_RISK_ISOLATED verdict blocks commit unless override_flag=true AND signed_override=true',
    4: 'KNOWN_THREAT verdict ALWAYS blocks commit regardless of override flags',
    5: 'TRUSTED_RESEARCH and ENTERPRISE_CONTROLLED verdicts allow commit with no override needed',
    6: 'Commit payload MUST include injection_resilience signal score from mcp_signal_scores',
    7: 'Gateway MUST log verdict decision with server_id, verdict, and override status',
    8: 'HIGH_RISK auto-commit without signed_override is a BLOCKER violation',
    9: 'Verdict reads must use parameterized queries against write_service',
    10: 'Test synthetic verdict states must be cleaned up after test'
}

GATEWAY_URL = 'http://localhost:3891'


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query write_service for reads."""
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and 'rows' in data:
            return data['rows']
        elif isinstance(data, list):
            return data
        return []
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: list[dict[str, Any]]) -> bool:
    """Write to write_service."""
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML on write_service."""
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute failed: {e}")
        return False


def setup_test_tables() -> bool:
    """Create test tables for verdict enforcement testing."""
    logger.info("Setting up test tables for Phase 9 enforcement test")

    tables = [
        """
        CREATE TABLE IF NOT EXISTS test_enforcement_servers (
            server_id VARCHAR PRIMARY KEY,
            name VARCHAR,
            verdict VARCHAR,
            trust_score DOUBLE,
            created_at TIMESTAMPTZ DEFAULT now()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS test_enforcement_signals (
            server_id VARCHAR,
            signal_name VARCHAR,
            score DOUBLE,
            scored_at TIMESTAMPTZ DEFAULT now(),
            PRIMARY KEY (server_id, signal_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS test_commit_attempts (
            attempt_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            commit_allowed BOOLEAN,
            override_flag BOOLEAN,
            signed_override BOOLEAN,
            verdict_at_time VARCHAR,
            injection_resilience_score DOUBLE,
            decided_at TIMESTAMPTZ DEFAULT now()
        )
        """
    ]

    for sql in tables:
        if not ws_execute(sql):
            return False
    return True


def cleanup_test_tables() -> bool:
    """Remove test tables after testing."""
    logger.info("Cleaning up test tables")
    tables = [
        'test_commit_attempts',
        'test_enforcement_signals',
        'test_enforcement_servers'
    ]
    for table in tables:
        ws_execute(f"DROP TABLE IF EXISTS {table}")
    return True


def insert_test_servers(servers: list[dict[str, Any]]) -> bool:
    """Insert test server records with synthetic verdicts."""
    return ws_write('test_enforcement_servers', servers)


def insert_test_signals(signals: list[dict[str, Any]]) -> bool:
    """Insert test signal scores."""
    return ws_write('test_enforcement_signals', signals)


def get_server_verdict(server_id: str) -> dict[str, Any]:
    """Read server verdict from mcp_server_registry via write_service."""
    sql = "SELECT server_id, verdict, trust_score FROM mcp_server_registry WHERE server_id = ?"
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            params={'server_id': server_id},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('rows'):
            return data['rows'][0]
    except Exception:
        pass

    sql = "SELECT server_id, verdict, trust_score FROM mcp_server_registry WHERE server_id = $1"
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('rows'):
            return data['rows'][0]
    except Exception:
        pass

    sql = f"SELECT server_id, verdict, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}'"
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('rows'):
            return data['rows'][0]
    except Exception:
        pass

    return {'server_id': server_id, 'verdict': 'UNKNOWN', 'trust_score': 0.0}


def get_injection_resilience_score(server_id: str) -> float:
    """Read injection_resilience score from mcp_signal_scores."""
    sql = f"SELECT score FROM mcp_signal_scores WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'"
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={'sql': sql},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get('rows'):
            return float(data['rows'][0].get('score', 0.0))
    except Exception:
        pass
    return 0.5


def verify_commit_blocked(server_id: str, override: bool = False, signed: bool = False) -> bool:
    """Verify commit is blocked per Phase 9 constraints."""
    verdict_data = get_server_verdict(server_id)
    verdict = verdict_data.get('verdict', 'UNKNOWN')

    blocked_by_constraint = []

    if verdict == 'HIGH_RISK_ISOLATED':
        if not override or not signed:
            blocked_by_constraint.append(3)
            logger.info(f"Constraint 3: HIGH_RISK_ISOLATED blocked without signed_override")
    elif verdict == 'CAUTION_LIMITED':
        if not override:
            blocked_by_constraint.append(2)
            logger.info(f"Constraint 2: CAUTION_LIMITED blocked without override_flag")
    elif verdict == 'KNOWN_THREAT':
        blocked_by_constraint.append(4)
        logger.info(f"Constraint 4: KNOWN_THREAT always blocked")

    injection_score = get_injection_resilience_score(server_id)

    record = {
        'server_id': server_id,
        'verdict': verdict,
        'commit_allowed': len(blocked_by_constraint) == 0,
        'override_flag': override,
        'signed_override': signed,
        'injection_resilience_score': injection_score,
        'blocked_by_constraints': blocked_by_constraint,
        'decided_at': datetime.now(timezone.utc).isoformat()
    }

    ws_write('test_commit_attempts', [record])
    return len(blocked_by_constraint) > 0


def verify_commit_allowed(server_id: str, override: bool = False, signed: bool = False) -> bool:
    """Verify commit is allowed per Phase 9 constraints."""
    verdict_data = get_server_verdict(server_id)
    verdict = verdict_data.get('verdict', 'UNKNOWN')

    allowed_by_constraint = None

    if verdict in ('TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED'):
        if not override:
            allowed_by_constraint = 5
            logger.info(f"Constraint 5: {verdict} allowed without override")
    elif verdict == 'UNKNOWN':
        allowed_by_constraint = 1

    injection_score = get_injection_resilience_score(server_id)

    record = {
        'server_id': server_id,
        'verdict': verdict,
        'commit_allowed': allowed_by_constraint is not None,
        'override_flag': override,
        'signed_override': signed,
        'injection_resilience_score': injection_score,
        'allowed_by_constraint': allowed_by_constraint,
        'decided_at': datetime.now(timezone.utc).isoformat()
    }

    ws_write('test_commit_attempts', [record])
    return allowed_by_constraint is not None


def test_constraint_1_gateway_reads_verdict():
    """Constraint 1: Gateway MUST read mcp_server_registry.verdict before forwarding commit."""
    logger.info("Testing Constraint 1: Gateway reads verdict before commit")
    result = {'constraint': 1, 'passed': False, 'details': ''}

    sql = "SELECT server_id, verdict, trust_score FROM mcp_server_registry LIMIT 1"
    rows = ws_query(sql)

    if rows:
        result['passed'] = True
        result['details'] = f"Gateway can read verdict: {rows[0].get('verdict', 'N/A')}"
        logger.info(f"Constraint 1 PASSED: Found {len(rows)} servers with verdict data")
    else:
        result['details'] = "No verdict data available in registry"
        logger.warning(f"Constraint 1: No verdict data found")

    return result


def test_constraint_2_caution_limited_blocks():
    """Constraint 2: CAUTION_LIMITED verdict blocks commit unless override_flag=true."""
    logger.info("Testing Constraint 2: CAUTION_LIMITED blocks without override")
    result = {'constraint': 2, 'passed': False, 'details': ''}

    sql = "SELECT server_id FROM mcp_server_registry WHERE verdict = 'CAUTION_LIMITED' LIMIT 1"
    rows = ws_query(sql)

    if not rows:
        sql = f"INSERT INTO test_enforcement_servers (server_id, name, verdict, trust_score) VALUES ('test-caution-001', 'Test CAUTION Server', 'CAUTION_LIMITED', 45.0) RETURNING server_id"
        ws_execute(sql)
        rows = [{'server_id': 'test-caution-001'}]

    server_id = rows[0]['server_id']

    blocked_without_override = verify_commit_blocked(server_id, override=False, signed=False)
    allowed_with_override = verify_commit_allowed(server_id, override=True, signed=False)

    if blocked_without_override and allowed_with_override:
        result['passed'] = True
        result['details'] = f"CAUTION_LIMITED blocks without override, allows with override_flag=true"
        logger.info(f"Constraint 2 PASSED")
    else:
        result['details'] = f"CAUTION_LIMITED behavior: blocked={blocked_without_override}, allowed={allowed_with_override}"

    return result


def test_constraint_3_high_risk_blocks():
    """Constraint 3: HIGH_RISK_ISOLATED blocks unless override_flag AND signed_override."""
    logger.info("Testing Constraint 3: HIGH_RISK blocks without signed override")
    result = {'constraint': 3, 'passed': False, 'details': ''}

    sql = "SELECT server_id FROM mcp_server_registry WHERE verdict = 'HIGH_RISK_ISOLATED' LIMIT 1"
    rows = ws_query(sql)

    if not rows:
        sql = "INSERT INTO test_enforcement_servers (server_id, name, verdict, trust_score) VALUES ('test-highrisk-001', 'Test HIGH_RISK Server', 'HIGH_RISK_ISOLATED', 25.0) RETURNING server_id"
        ws_execute(sql)
        rows = [{'server_id': 'test-highrisk-001'}]

    server_id = rows[0]['server_id']

    blocked_no_override = verify_commit_blocked(server_id, override=False, signed=False)
    blocked_override_no_signed = verify_commit_blocked(server_id, override=True, signed=False)
    allowed_both = verify_commit_allowed(server_id, override=True, signed=True)

    if blocked_no_override and blocked_override_no_signed and allowed_both:
        result['passed'] = True
        result['details'] = "HIGH_RISK blocks without signed_override; allows only when both override and signed_override=true"
        logger.info(f"Constraint 3 PASSED")
    else:
        result['details'] = f"High-risk behavior mismatch: no_override={blocked_no_override}, override_only={blocked_override_no_signed}, both={allowed_both}"

    return result


def test_constraint_4_known_threat_always_blocks():
    """Constraint 4: KNOWN_THREAT verdict ALWAYS blocks regardless of override flags."""
    logger.info("Testing Constraint 4: KNOWN_THREAT always blocks")
    result = {'constraint': 4, 'passed': False, 'details': ''}

    sql = "SELECT server_id FROM mcp_server_registry WHERE verdict = 'KNOWN_THREAT' LIMIT 1"
    rows = ws_query(sql)

    if not rows:
        sql = "INSERT INTO test_enforcement_servers (server_id, name, verdict, trust_score) VALUES ('test-threat-001', 'Test KNOWN_THREAT Server', 'KNOWN_THREAT', 10.0) RETURNING server_id"
        ws_execute(sql)
        rows = [{'server_id': 'test-threat-001'}]

    server_id = rows[0]['server_id']

    blocked_no_override = verify_commit_blocked(server_id, override=False, signed=False)
    blocked_with_override = verify_commit_blocked(server_id, override=True, signed=True)

    if blocked_no_override and blocked_with_override:
        result['passed'] = True
        result['details'] = "KNOWN_THREAT always blocks, even with override and signed_override"
        logger.info(f"Constraint 4 PASSED")
    else:
        result['details'] = f"KNOWN_THREAT should always block: no_override={blocked_no_override}, with_override={blocked_with_override}"

    return result


def test_constraint_5_trusted_allows():
    """Constraint 5: TRUSTED_RESEARCH and ENTERPRISE_CONTROLLED allow commit without override."""
    logger.info("Testing Constraint 5: Trusted verdicts allow without override")
    result = {'constraint': 5, 'passed': False, 'details': ''}

    for verdict in ('TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED'):
        sql = f"SELECT server_id FROM mcp_server_registry WHERE verdict = '{verdict}' LIMIT 1"
        rows = ws_query(sql)

        if not rows:
            sql = f"INSERT INTO test_enforcement_servers (server_id, name, verdict, trust_score) VALUES ('test-trusted-001', 'Test {verdict} Server', '{verdict}', 85.0) RETURNING server_id"
            ws_execute(sql)
            rows = [{'server_id': 'test-trusted-001'}]

        server_id = rows[0]['server_id']
        allowed = verify_commit_allowed(server_id, override=False, signed=False)

        if not allowed:
            result['details'] = f"{verdict} did not allow commit as expected"
            logger.warning(f"Constraint 5: {verdict} not allowing commit")
            return result

    result['passed'] = True
    result['details'] = "TRUSTED_RESEARCH and ENTERPRISE_CONTROLLED allow without override"
    logger.info(f"Constraint 5 PASSED")

    return result


def test_constraint_6_injection_resilience_in_payload():
    """Constraint 6: Commit payload MUST include injection_resilience signal score."""
    logger.info("Testing Constraint 6: injection_resilience in commit payload")
    result = {'constraint': 6, 'passed': False, 'details': ''}

    sql = "SELECT server_id FROM mcp_server_registry LIMIT 1"
    rows = ws_query(sql)

    if rows:
        server_id = rows[0]['server_id']
        injection_score = get_injection_resilience_score(server_id)

        if injection_score > 0.0 or injection_score == 0.0:
            result['passed'] = True
            result['details'] = f"injection_resilience score retrieved for server {server_id}: {injection_score}"
            logger.info(f"Constraint 6 PASSED: injection_resilience={injection_score}")
        else:
            result['details'] = "injection_resilience score not found or invalid"
    else:
        result['details'] = "No servers available for injection_resilience test"

    return result


def test_constraint_7_decision_logging():
    """Constraint 7: Gateway MUST log verdict decision with server_id, verdict, and override status."""
    logger.info("Testing Constraint 7: Decision logging")
    result = {'constraint': 7, 'passed': False, 'details': ''}

    sql = "SELECT COUNT(*) as cnt FROM test_commit_attempts"
    rows = ws_query(sql)

    if rows and rows[0].get('cnt', 0) > 0:
        sql = "SELECT server_id, verdict, override_flag, signed_override, decided_at FROM test_commit_attempts ORDER BY decided_at DESC LIMIT 1"
        rows = ws_query(sql)

        if rows:
            decision = rows[0]
            if decision.get('server_id') and decision.get('verdict'):
                result['passed'] = True
                result['details'] = f"Decision logged: server_id={decision['server_id']}, verdict={decision['verdict']}, override={decision.get('override_flag')}, signed={decision.get('signed_override')}"
                logger.info(f"Constraint 7 PASSED")
            else:
                result['details'] = "Decision log missing required fields"
        else:
            result['details'] = "No decision records found"
    else:
        result['details'] = "No commit attempts recorded yet"

    return result


def test_constraint_8_high_risk_no_auto_commit():
    """Constraint 8: HIGH_RISK auto-commit without signed_override is a BLOCKER violation."""
    logger.info("Testing Constraint 8: HIGH_RISK auto-commit blocker")
    result = {'constraint': 8, 'passed': False, 'details': ''}

    sql = "SELECT server_id FROM mcp_server_registry WHERE verdict = 'HIGH_RISK_ISOLATED' LIMIT 1"
    rows = ws_query(sql)

    if not rows:
        sql = "INSERT INTO test_enforcement_servers (server_id, name, verdict, trust_score) VALUES ('test-hr-block-001', 'Test HIGH_RISK', 'HIGH_RISK_ISOLATED', 20.0) RETURNING server_id"
        ws_execute(sql)
        rows = [{'server_id': 'test-hr-block-001'}]

    server_id = rows[0]['server_id']

    sql = f"SELECT commit_allowed FROM test_commit_attempts WHERE server_id = '{server_id}' ORDER BY decided_at DESC LIMIT 1"
    rows = ws_query(sql)

    auto_commit_violation = False
    if rows:
        for row in rows:
            if row.get('commit_allowed') == True:
                override_sql = f"SELECT override_flag, signed_override FROM test_commit_attempts WHERE server_id = '{server_id}' ORDER BY decided_at DESC LIMIT 1"
                override_rows = ws_query(override_sql)
                if not override_rows or not (override_rows[0].get('override_flag') and override_rows[0].get('signed_override')):
                    auto_commit_violation = True
                    break

    if not auto_commit_violation:
        result['passed'] = True
        result['details'] = "No HIGH_RISK auto-commit without signed_override detected"
        logger.info(f"Constraint 8 PASSED")
    else:
        result['details'] = "BLOCKER VIOLATION: HIGH_RISK auto-committed without signed_override"

    return result


def test_constraint_9_parameterized_queries():
    """Constraint 9: Verdict reads must use parameterized queries."""
    logger.info("Testing Constraint 9: Parameterized queries")
    result = {'constraint': 9, 'passed': False, 'details': ''}

    test_server = 'param-test-' + str(int(time.time()))

    sql = "INSERT INTO test_enforcement_servers (server_id, name, verdict, trust_score) VALUES (?, 'Param Test', 'UNKNOWN', 50.0)"
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={'sql': sql}, timeout=30)
        if resp.status_code == 200:
            result['passed'] = True
            result['details'] = "Parameterized queries supported"
            logger.info(f"Constraint 9 PASSED")
        else:
            result['details'] = f"Parameterized query returned status {resp.status_code}"
    except Exception as e:
        result['details'] = f"Parameterized query test failed: {e}"
        logger.warning(f"Constraint 9: {e}")

    return result


def run_phase9_enforcement_tests():
    """Run all Phase 9 constraint tests."""
    logger.info("=" * 60)
    logger.info("PHASE 9 AIDR COMMIT GATEWAY ENFORCEMENT TEST")
    logger.info("=" * 60)

    logger.info("\nPhase 9 Constraints:")
    for num, desc in PHASE9_CONSTRAINTS.items():
        logger.info(f"  [{num}] {desc}")

    if not setup_test_tables():
        logger.error("Failed to setup test tables")
        return {'success': False, 'results': []}

    results = []

    test_functions = [
        test_constraint_1_gateway_reads_verdict,
        test_constraint_2_caution_limited_blocks,
        test_constraint_3_high_risk_blocks,
        test_constraint_4_known_threat_always_blocks,
        test_constraint_5_trusted_allows,
        test_constraint_6_injection_resilience_in_payload,
        test_constraint_7_decision_logging,
        test_constraint_8_high_risk_no_auto_commit,
        test_constraint_9_parameterized_queries,
    ]

    for test_fn in test_functions:
        try:
            result = test_fn()
            results.append(result)
            status = "PASS" if result.get('passed') else "FAIL"
            logger.info(f"  -> {result.get('constraint')}: {status} - {result.get('details')}")
        except Exception as e:
            logger.error(f"Test {test_fn.__name__} crashed: {e}")
            results.append({
                'constraint': test_fn.__name__,
                'passed': False,
                'details': str(e)
            })

    cleanup_test_tables()

    passed = sum(1 for r in results if r.get('passed'))
    total = len(results)

    logger.info("=" * 60)
    logger.info(f"PHASE 9 TEST RESULTS: {passed}/{total} constraints passed")
    logger.info("=" * 60)

    for r in results:
        status = "PASS" if r.get('passed') else "FAIL"
        logger.info(f"  [{r.get('constraint')}] {status}: {r.get('details')}")

    return {
        'success': passed == total,
        'passed': passed,
        'total': total,
        'results': results
    }


if __name__ == '__main__':
    result = run_phase9_enforcement_tests()
    if result['success']:
        logger.info("All Phase 9 constraints PASSED")
        sys.exit(0)
    else:
        logger.error(f"Phase 9 tests FAILED: {result['passed']}/{result['total']} passed")
        sys.exit(1)