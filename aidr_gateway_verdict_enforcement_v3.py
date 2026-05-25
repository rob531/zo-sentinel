import logging
import time
import json
import os
import signal
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler('/home/workspace/logs/aidr_gateway_verdict_enforcement_v3.log')]
)
log = logging.getLogger('aidr_gateway_verdict_enforcement_v3')

SERVICE_NAME = 'aidr_gateway_verdict_enforcement_v3'
SERVICE_PORT = 0
PID_FILE = '/tmp/aidr_gateway_verdict_enforcement_v3.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'

VERDICT_BLOCKED = {'HIGH_RISK_ISOLATED', 'KNOWN_THREAT'}
VERDICT_CAUTION = {'CAUTION_LIMITED', 'AMBER_UNVERIFIED'}
VERDICT_ALLOWED = {'TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED'}

PROTECTED_VERDICTS = VERDICT_BLOCKED | VERDICT_CAUTION

_request_id_counter = 0


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_request_id(server_id: str, commit_ref: str) -> str:
    global _request_id_counter
    _request_id_counter += 1
    data = f"{server_id}:{commit_ref}:{_request_id_counter}:{utc_now_iso()}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def ws_query(sql: str, params: Optional[List] = None) -> Optional[List[Dict[str, Any]]]:
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        if result.get('error'):
            log.error(f"Query error: {result['error']}")
            return None
        return result.get('rows', [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return None


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(WRITE_SERVICE_URL + '/write', json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def send_heartbeat() -> None:
    ts = utc_now_iso()
    rows = [{"service": SERVICE_NAME, "last_heartbeat": ts, "status": "running", "meta": "{}"}]
    ws_write("service_health", rows)


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error(f"Another instance running with PID {old_pid}. Exiting.")
            return False
        except (OSError, ValueError):
            log.warning(f"Stale PID file {old_pid}. Overwriting.")
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file() -> None:
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame) -> None:
    log.info(f"Received signal {signum}. Shutting down.")
    remove_pid_file()
    sys.exit(0)


def get_server_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT verdict, trust_score, risk_tier, threat_count, computed_at
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    rows = ws_query(sql, [server_id])
    if not rows:
        log.warning(f"No registry entry for server_id={server_id}")
        return None
    return rows[0]


def get_signal_scores(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    ORDER BY signal_name
    """
    rows = ws_query(sql, [server_id])
    return rows if rows else []


def get_injection_resilience_score(server_id: str) -> Optional[float]:
    sql = """
    SELECT score FROM mcp_signal_scores
    WHERE server_id = ? AND signal_name = 'injection_resilience'
    """
    rows = ws_query(sql, [server_id])
    if rows and len(rows) > 0:
        return float(rows[0].get('score', 0.0))
    return None


def get_threat_associations(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT threat_type, severity, evidence, reported_at
    FROM mcp_threat_associations
    WHERE server_id = ?
    ORDER BY reported_at DESC
    """
    rows = ws_query(sql, [server_id])
    return rows if rows else []


def check_verdict_blocked(verdict: str) -> bool:
    return verdict in VERDICT_BLOCKED


def check_verdict_requires_override(verdict: str) -> bool:
    return verdict in VERDICT_CAUTION


def check_verdict_allowed(verdict: str) -> bool:
    return verdict in VERDICT_ALLOWED


def build_commit_payload(server_id: str, commit_ref: str, override_flag: bool = False) -> Dict[str, Any]:
    verdict_data = get_server_verdict(server_id)
    signals = get_signal_scores(server_id)
    injection_score = get_injection_resilience_score(server_id)
    threats = get_threat_associations(server_id)

    verdict = verdict_data.get('verdict', 'UNKNOWN') if verdict_data else 'UNKNOWN'
    trust_score = verdict_data.get('trust_score', 0.0) if verdict_data else 0.0
    risk_tier = verdict_data.get('risk_tier', 'UNKNOWN') if verdict_data else 'UNKNOWN'

    signal_map = {s['signal_name']: s['score'] for s in signals}
    all_signal_scores = {
        'injection_resilience': injection_score,
        'supply_chain': signal_map.get('supply_chain'),
        'community_signal': signal_map.get('community_signal'),
        'temporal_stability': signal_map.get('temporal_stability'),
        'permission_scope': signal_map.get('permission_scope'),
        'tool_description_safety': signal_map.get('tool_description_safety'),
    }

    return {
        'server_id': server_id,
        'commit_ref': commit_ref,
        'verdict': verdict,
        'trust_score': trust_score,
        'risk_tier': risk_tier,
        'injection_resilience_score': injection_score,
        'all_signal_scores': all_signal_scores,
        'threat_associations': threats,
        'override_flag': override_flag,
        'enforcement_timestamp': utc_now_iso(),
    }


def enforce_commit(server_id: str, commit_ref: str, override: bool = False) -> Dict[str, Any]:
    req_id = compute_request_id(server_id, commit_ref)

    log.info(f"[{req_id}] Verdict enforcement request: server={server_id} commit={commit_ref} override={override}")

    verdict_data = get_server_verdict(server_id)
    if not verdict_data:
        result = {
            'request_id': req_id,
            'server_id': server_id,
            'commit_ref': commit_ref,
            'verdict': 'UNKNOWN',
            'action': 'BLOCKED',
            'reason': 'No registry entry found',
            'timestamp': utc_now_iso(),
        }
        log_warning(req_id, server_id, commit_ref, 'UNKNOWN', 'No registry entry')
        return result

    verdict = verdict_data.get('verdict', 'UNKNOWN')
    trust_score = verdict_data.get('trust_score', 0.0)
    risk_tier = verdict_data.get('risk_tier', 'UNKNOWN')

    log.info(f"[{req_id}] Found verdict={verdict} trust_score={trust_score} risk_tier={risk_tier}")

    if check_verdict_blocked(verdict):
        result = {
            'request_id': req_id,
            'server_id': server_id,
            'commit_ref': commit_ref,
            'verdict': verdict,
            'trust_score': trust_score,
            'risk_tier': risk_tier,
            'action': 'BLOCKED',
            'reason': f'Verdict {verdict} is in blocked list (HIGH_RISK_ISOLATED/KNOWN_THREAT)',
            'override_attempted': override,
            'override_accepted': False,
            'timestamp': utc_now_iso(),
        }
        log_blocked(req_id, server_id, commit_ref, verdict)
        audit_commit_decision(req_id, server_id, commit_ref, verdict, 'BLOCKED', result['reason'])
        return result

    if check_verdict_requires_override(verdict):
        if not override:
            result = {
                'request_id': req_id,
                'server_id': server_id,
                'commit_ref': commit_ref,
                'verdict': verdict,
                'trust_score': trust_score,
                'risk_tier': risk_tier,
                'action': 'BLOCKED',
                'reason': f'Verdict {verdict} requires explicit override flag',
                'override_provided': False,
                'timestamp': utc_now_iso(),
            }
            log_caution_blocked(req_id, server_id, commit_ref, verdict)
            audit_commit_decision(req_id, server_id, commit_ref, verdict, 'BLOCKED', result['reason'])
            return result

        result = {
            'request_id': req_id,
            'server_id': server_id,
            'commit_ref': commit_ref,
            'verdict': verdict,
            'trust_score': trust_score,
            'risk_tier': risk_tier,
            'action': 'ALLOWED_WITH_OVERRIDE',
            'reason': f'Verdict {verdict} allowed with explicit override',
            'override_provided': True,
            'override_accepted': True,
            'timestamp': utc_now_iso(),
        }
        log_override_allowed(req_id, server_id, commit_ref, verdict)
        audit_commit_decision(req_id, server_id, commit_ref, verdict, 'ALLOWED_WITH_OVERRIDE', 'Override provided')
        return result

    signal_scores = get_signal_scores(server_id)
    injection_score = get_injection_resilience_score(server_id)
    threats = get_threat_associations(server_id)

    result = {
        'request_id': req_id,
        'server_id': server_id,
        'commit_ref': commit_ref,
        'verdict': verdict,
        'trust_score': trust_score,
        'risk_tier': risk_tier,
        'injection_resilience_score': injection_score,
        'all_signal_scores': {s['signal_name']: s['score'] for s in signal_scores},
        'threat_count': len(threats),
        'action': 'ALLOWED',
        'reason': f'Verdict {verdict} is in allowed list',
        'timestamp': utc_now_iso(),
    }
    log_allowed(req_id, server_id, commit_ref, verdict, trust_score)
    audit_commit_decision(req_id, server_id, commit_ref, verdict, 'ALLOWED', result['reason'])

    return result


def log_blocked(req_id: str, server_id: str, commit_ref: str, verdict: str) -> None:
    log.critical(f"[{req_id}] COMMIT BLOCKED: server={server_id} commit={commit_ref} verdict={verdict} - CRITICAL THREAT")


def log_caution_blocked(req_id: str, server_id: str, commit_ref: str, verdict: str) -> None:
    log.warning(f"[{req_id}] COMMIT BLOCKED: server={server_id} commit={commit_ref} verdict={verdict} - Requires override")


def log_override_allowed(req_id: str, server_id: str, commit_ref: str, verdict: str) -> None:
    log.warning(f"[{req_id}] COMMIT ALLOWED WITH OVERRIDE: server={server_id} commit={commit_ref} verdict={verdict}")


def log_allowed(req_id: str, server_id: str, commit_ref: str, verdict: str, trust_score: float) -> None:
    log.info(f"[{req_id}] COMMIT ALLOWED: server={server_id} commit={commit_ref} verdict={verdict} trust={trust_score}")


def log_warning(req_id: str, server_id: str, commit_ref: str, verdict: str, reason: str) -> None:
    log.warning(f"[{req_id}] COMMIT BLOCKED: server={server_id} commit={commit_ref} verdict={verdict} reason={reason}")


def audit_commit_decision(request_id: str, server_id: str, commit_ref: str, verdict: str, action: str, reason: str) -> None:
    audit_row = {
        'target_server_id': server_id,
        'event_type': 'AIDR_COMMIT_GATEWAY_ENFORCEMENT',
        'actor': 'aidr_gateway_verdict_enforcement_v3',
        'detail': json.dumps({
            'request_id': request_id,
            'commit_ref': commit_ref,
            'verdict': verdict,
            'action': action,
            'reason': reason,
            'timestamp': utc_now_iso(),
        }),
        'created_at': utc_now_iso(),
    }
    ws_write('audit_log', [audit_row])


def cycle() -> Dict[str, Any]:
    stats = {
        'cycle_time': utc_now_iso(),
        'decisions_processed': 0,
        'blocked': 0,
        'allowed': 0,
        'override_allowed': 0,
        'unknown': 0,
    }
    return stats


def run() -> None:
    log.info("Starting AIDR Gateway Verdict Enforcement v3")

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    if not check_single_instance():
        sys.exit(1)

    log.info(f"Verdict enforcement active. Blocked={VERDICT_BLOCKED}, Caution={VERDICT_CAUTION}, Allowed={VERDICT_ALLOWED}")

    POLL_SECS = 30
    last_heartbeat = time.time()

    while True:
        try:
            stats = cycle()
            send_heartbeat()

            now = time.time()
            if now - last_heartbeat >= POLL_SECS:
                log.debug(f"Heartbeat sent. Stats: {stats}")
                last_heartbeat = now

        except Exception as e:
            log.error(f"Error in main loop: {e}")

        time.sleep(POLL_SECS)


if __name__ == '__main__':
    import sys
    run()