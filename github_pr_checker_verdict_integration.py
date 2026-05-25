import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename='/home/workspace/logs/github_pr_checker_verdict_integration.log'
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'github_pr_checker_verdict_integration'
SERVICE_PORT = 0
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
WRITE_URL = 'http://localhost:8772/write'
PID_FILE = '/tmp/github_pr_checker_verdict_integration.pid'
POLL_SECS = 30
HTTP_TIMEOUT = 15

BLOCKED_VERDICTS = {'CAUTION_LIMITED', 'HIGH_RISK_ISOLATED', 'KNOWN_THREAT'}
ALLOWED_VERDICTS = {'TRUSTED_GENERAL', 'TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED'}
AMBERS = {'AMBER_UNVERIFIED', 'AMBER_REVIEW', 'AMBER_LIMITED'}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_single_instance() -> bool:
    pid_path = PID_FILE
    if os.path.exists(pid_path):
        try:
            with open(pid_path) as f:
                old_pid = int(f.read().strip())
            if old_pid != os.getpid():
                import subprocess
                result = subprocess.run(['ps', '-p', str(old_pid)], capture_output=True)
                if result.returncode == 0:
                    log.error(f"Another instance already running as PID {old_pid}")
                    return False
        except (ValueError, subprocess.CalledProcessError):
            pass
    try:
        with open(pid_path, 'w') as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log.error(f"Failed to write PID file: {e}")
    return True


def remove_pid_file() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.warning(f"Failed to remove PID file: {e}")


def signal_handler(signum: int, frame: Any) -> None:
    signame = signal.Signals(signum).name
    log.info(f"Received {signame}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def ws_query(sql: str) -> list:
    try:
        resp = requests.post(
            QUERY_URL,
            json={'sql': sql},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f"ws_query failed: {e} | SQL: {sql[:200]}")
        return []


def ws_write(table: str, rows: list) -> bool:
    try:
        resp = requests.post(
            WRITE_URL,
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e} | table: {table}")
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_URL,
            json={'sql': sql},
            timeout=HTTP_TIMEOUT
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e} | SQL: {sql[:200]}")
        return False


def send_heartbeat() -> None:
    now = utc_now_iso()
    rows = [{
        'service': SERVICE_NAME,
        'last_heartbeat': now,
        'status': 'ok',
        'ts': now,
        'meta': '{}'
    }]
    ws_write('service_health', rows)


def ensure_tables() -> None:
    sql = """
    CREATE TABLE IF NOT EXISTS github_pr_verdict_checks (
        check_id VARCHAR PRIMARY KEY,
        pr_url VARCHAR NOT NULL,
        pr_number INTEGER,
        repository VARCHAR,
        server_id VARCHAR,
        server_name VARCHAR,
        verdict VARCHAR,
        risk_tier VARCHAR,
        can_merge BOOLEAN,
        block_reason VARCHAR,
        checked_at TIMESTAMPTZ NOT NULL
    )
    """
    ws_execute(sql)

    audit_sql = """
    CREATE TABLE IF NOT EXISTS github_pr_verdict_audit (
        audit_id VARCHAR PRIMARY KEY,
        check_id VARCHAR,
        pr_url VARCHAR,
        event_type VARCHAR,
        actor VARCHAR,
        detail VARCHAR,
        created_at TIMESTAMPTZ NOT NULL
    )
    """
    ws_execute(audit_sql)
    log.info("Tables ensured")


def get_server_verdict(server_id: str) -> dict:
    sql = f"""
    SELECT 
        r.server_id,
        r.name,
        r.verdict,
        r.risk_tier,
        r.trust_score,
        r.last_assessed
    FROM mcp_server_registry r
    WHERE r.server_id = '{server_id}'
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return {}


def get_server_verdict_by_name(server_name: str) -> dict:
    sql = f"""
    SELECT 
        server_id,
        name,
        verdict,
        risk_tier,
        trust_score,
        last_assessed
    FROM mcp_server_registry
    WHERE LOWER(name) = LOWER('{server_name}')
       OR name LIKE '%{server_name}%'
    ORDER BY trust_score DESC NULLS LAST
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return {}


def get_signal_scores(server_id: str) -> list:
    sql = f"""
    SELECT 
        signal_name,
        score,
        evidence,
        scored_at
    FROM mcp_signal_scores
    WHERE server_id = '{server_id}'
    ORDER BY signal_name
    """
    return ws_query(sql)


def get_recent_github_pr_checks(limit: int = 50) -> list:
    sql = f"""
    SELECT 
        check_id,
        pr_url,
        pr_number,
        repository,
        server_id,
        server_name,
        verdict,
        risk_tier,
        can_merge,
        block_reason,
        checked_at
    FROM github_pr_verdict_checks
    ORDER BY checked_at DESC
    LIMIT {limit}
    """
    return ws_query(sql)


def compute_verdict_decision(server_id: str, server_name: str, pr_url: str) -> dict:
    verdict_data = get_server_verdict(server_id)
    if not verdict_data:
        verdict_data = get_server_verdict_by_name(server_name)

    if not verdict_data:
        return {
            'verdict': 'UNKNOWN',
            'risk_tier': 'UNASSESSED',
            'can_merge': False,
            'block_reason': f'MCP server "{server_name}" not found in ZO-SENTINEL registry. Manual review required.'
        }

    verdict = verdict_data.get('verdict', 'UNKNOWN')
    risk_tier = verdict_data.get('risk_tier', 'UNKNOWN')

    can_merge = True
    block_reason = None

    if verdict in BLOCKED_VERDICTS:
        can_merge = False
        block_reason = f'Verdict "{verdict}" is blocked per security policy. Risk tier: {risk_tier}'

    if verdict in AMBERS and risk_tier in {'HIGH_RISK', 'CRITICAL'}:
        can_merge = False
        block_reason = f'Verdict "{verdict}" with risk tier "{risk_tier}" requires manual approval. Automated merge blocked.'

    return {
        'verdict': verdict,
        'risk_tier': risk_tier,
        'trust_score': verdict_data.get('trust_score'),
        'can_merge': can_merge,
        'block_reason': block_reason
    }


def check_verdict_for_mcp_servers(mcp_servers: list, pr_url: str, pr_number: int = None, repository: str = None) -> list:
    results = []
    import hashlib

    for server in mcp_servers:
        server_id = server.get('server_id', '')
        server_name = server.get('name', server.get('server_name', ''))
        url = server.get('url', '')

        if not server_id and server_name:
            server_data = get_server_verdict_by_name(server_name)
            if server_data:
                server_id = server_data.get('server_id', '')

        decision = compute_verdict_decision(server_id, server_name, pr_url)

        check_id = hashlib.sha256(
            f"{pr_url}:{server_id}:{server_name}:{utc_now_iso()}".encode()
        ).hexdigest()[:32]

        check_record = {
            'check_id': check_id,
            'pr_url': pr_url,
            'pr_number': pr_number,
            'repository': repository,
            'server_id': server_id,
            'server_name': server_name,
            'verdict': decision['verdict'],
            'risk_tier': decision['risk_tier'],
            'can_merge': decision['can_merge'],
            'block_reason': decision.get('block_reason', ''),
            'checked_at': utc_now_iso()
        }

        ws_write('github_pr_verdict_checks', [check_record])

        audit_record = {
            'audit_id': hashlib.sha256(
                f"audit:{check_id}:{utc_now_iso()}".encode()
            ).hexdigest()[:32],
            'check_id': check_id,
            'pr_url': pr_url,
            'event_type': 'PR_VERDICT_CHECK',
            'actor': 'github_pr_checker_verdict_integration',
            'detail': f"Server: {server_name}, Verdict: {decision['verdict']}, CanMerge: {decision['can_merge']}",
            'created_at': utc_now_iso()
        }
        ws_write('github_pr_verdict_audit', [audit_record])

        results.append({
            'check_id': check_id,
            'server_id': server_id,
            'server_name': server_name,
            'url': url,
            'verdict': decision['verdict'],
            'risk_tier': decision['risk_tier'],
            'can_merge': decision['can_merge'],
            'block_reason': decision.get('block_reason', '')
        })

    return results


def get_pending_pr_checks() -> list:
    sql = """
    SELECT 
        check_id,
        pr_url,
        pr_number,
        repository,
        server_id,
        server_name,
        verdict,
        risk_tier,
        can_merge,
        block_reason,
        checked_at
    FROM github_pr_verdict_checks
    WHERE checked_at >= NOW() - INTERVAL '1 hour'
    ORDER BY checked_at DESC
    LIMIT 100
    """
    return ws_query(sql)


def get_high_risk_unblocked_servers() -> list:
    sql = """
    SELECT 
        server_id,
        name,
        url,
        verdict,
        risk_tier,
        trust_score
    FROM mcp_server_registry
    WHERE verdict IN ('CAUTION_LIMITED', 'HIGH_RISK_ISOLATED', 'KNOWN_THREAT')
       OR (verdict LIKE 'AMBER%' AND risk_tier IN ('HIGH_RISK', 'CRITICAL'))
    ORDER BY trust_score ASC NULLS LAST
    LIMIT 50
    """
    return ws_query(sql)


def generate_verdict_report() -> dict:
    total_checks_sql = "SELECT COUNT(*) as total FROM github_pr_verdict_checks"
    total_rows = ws_query(total_checks_sql)
    total_checks = total_rows[0]['total'] if total_rows else 0

    blocked_sql = """
    SELECT COUNT(*) as blocked 
    FROM github_pr_verdict_checks 
    WHERE can_merge = false
    """
    blocked_rows = ws_query(blocked_sql)
    blocked_count = blocked_rows[0]['blocked'] if blocked_rows else 0

    allowed_sql = """
    SELECT COUNT(*) as allowed 
    FROM github_pr_verdict_checks 
    WHERE can_merge = true
    """
    allowed_rows = ws_query(allowed_sql)
    allowed_count = allowed_rows[0]['allowed'] if allowed_rows else 0

    recent_checks = get_recent_github_pr_checks(10)

    return {
        'total_checks': total_checks,
        'blocked_count': blocked_count,
        'allowed_count': allowed_count,
        'block_rate': round(blocked_count / total_checks * 100, 2) if total_checks > 0 else 0,
        'recent_checks': recent_checks
    }


def cycle() -> int:
    log.info("Running verdict integration cycle")
    ensure_tables()

    report = generate_verdict_report()
    log.info(f"Verdict report: {report['total_checks']} total checks, {report['blocked_count']} blocked, {report['allowed_count']} allowed")

    high_risk = get_high_risk_unblocked_servers()
    if high_risk:
        log.info(f"Found {len(high_risk)} high-risk servers in registry")

    pending = get_pending_pr_checks()
    log.info(f"Found {len(pending)} pending PR verdict checks")

    send_heartbeat()
    return 0


def run() -> None:
    log.info(f"Starting {SERVICE_NAME}")

    if not check_single_instance():
        log.error("Cannot acquire PID file, another instance running")
        sys.exit(1)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    try:
        ensure_tables()
    except Exception as e:
        log.error(f"Failed to initialize tables: {e}")

    log.info(f"{SERVICE_NAME} running, polling every {POLL_SECS}s")

    while True:
        try:
            cycle()
        except Exception as e:
            log.error(f"Error in cycle: {e}", exc_info=True)

        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()