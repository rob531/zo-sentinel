import os
import sys
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import requests

WRITE_SERVICE_URL = os.environ.get(
    "WRITE_SERVICE_URL",
    "http://localhost:8772"
)
QUERY_SERVICE_URL = os.environ.get(
    "QUERY_SERVICE_URL",
    "http://localhost:8772"
)
EXECUTE_SERVICE_URL = os.environ.get(
    "EXECUTE_SERVICE_URL",
    "http://localhost:8772"
)
SERVICE_NAME = "score_results_push_verifier"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = os.path.dirname(f"/home/workspace/logs/{SERVICE_NAME}.log")
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)


def ws_query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql)
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error("ws_write failed for table %s: %s", table, e)
        return False


def ws_execute(sql: str, params: Optional[Dict[str, Any]] = None) -> bool:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{EXECUTE_SERVICE_URL}/execute",
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error("ws_execute failed: %s | SQL: %s", e, sql)
        return False


def check_single_instance() -> None:
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        old = open(PID_FILE).read().strip()
        if old and old != pid:
            log.error("Another instance already running (PID %s). Exiting.", old)
            sys.exit(1)
    with open(PID_FILE, "w") as f:
        f.write(pid)


def remove_pid_file() -> None:
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum: int, frame) -> None:
    log.info("Received signal %d, shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_idempotency_key(server_id: str, check_type: str) -> str:
    raw = f"{server_id}:{check_type}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def ensure_verification_results_table() -> None:
    create_sql = """
    CREATE TABLE IF NOT EXISTS score_push_verification_results (
        verification_id    VARCHAR(64) PRIMARY KEY,
        server_id         VARCHAR(128) NOT NULL,
        check_type        VARCHAR(64) NOT NULL,
        passed            BOOLEAN NOT NULL,
        details_json      VARCHAR(2048),
        computed_at       TIMESTAMPTZ NOT NULL,
        UNIQUE(server_id, check_type)
    )
    """
    ws_execute(create_sql)


def get_registry_servers(limit: int = 100) -> List[Dict[str, Any]]:
    sql = """
    SELECT server_id, name, trust_score, verdict, risk_tier, last_assessed
    FROM mcp_server_registry
    ORDER BY last_assessed DESC NULLS LAST
    LIMIT %s
    """
    return ws_query(sql, {"limit": limit})


def get_signal_scores_for_server(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = %s
    ORDER BY scored_at DESC
    """
    return ws_query(sql, {"server_id": server_id})


def get_risk_register_for_server(server_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT risk_tier, risk_rank, threat_count, computed_at
    FROM mcp_risk_register
    WHERE server_id = %s
    ORDER BY computed_at DESC
    LIMIT 1
    """
    rows = ws_query(sql, {"server_id": server_id})
    return rows[0] if rows else None


def verify_signal_scores_populated(server_id: str) -> Dict[str, Any]:
    scores = get_signal_scores_for_server(server_id)
    signal_count = len(scores)
    passed = signal_count > 0
    details = {
        "signal_count": signal_count,
        "signals": [s.get("signal_name") for s in scores]
    }
    return {
        "check_type": "signal_scores_populated",
        "passed": passed,
        "details": details,
        "server_id": server_id
    }


def verify_risk_register_match(server_id: str) -> Dict[str, Any]:
    risk = get_risk_register_for_server(server_id)
    if risk is None:
        return {
            "check_type": "risk_register_match",
            "passed": False,
            "details": {"reason": "No risk_register entry found"},
            "server_id": server_id
        }
    registry = ws_query(
        "SELECT risk_tier FROM mcp_server_registry WHERE server_id = %s LIMIT 1",
        {"server_id": server_id}
    )
    registry_risk_tier = registry[0].get("risk_tier") if registry else None
    passed = registry_risk_tier == risk.get("risk_tier")
    return {
        "check_type": "risk_register_match",
        "passed": passed,
        "details": {
            "registry_risk_tier": registry_risk_tier,
            "risk_register_tier": risk.get("risk_tier")
        },
        "server_id": server_id
    }


def verify_trust_score_consistency(server_id: str) -> Dict[str, Any]:
    registry_rows = ws_query(
        "SELECT trust_score, verdict FROM mcp_server_registry WHERE server_id = %s LIMIT 1",
        {"server_id": server_id}
    )
    if not registry_rows:
        return {
            "check_type": "trust_score_consistency",
            "passed": False,
            "details": {"reason": "Server not found in registry"},
            "server_id": server_id
        }
    row = registry_rows[0]
    trust_score = row.get("trust_score")
    verdict = row.get("verdict", "").upper()

    if trust_score is None:
        return {
            "check_type": "trust_score_consistency",
            "passed": False,
            "details": {"reason": "trust_score is NULL"},
            "server_id": server_id
        }

    if verdict == "TRUSTED":
        passed = trust_score >= 70.0
    elif verdict == "AMBER":
        passed = 40.0 <= trust_score < 70.0
    elif verdict == "UNTRUSTED":
        passed = trust_score < 40.0
    elif verdict == "KNOWN_THREAT":
        passed = trust_score < 20.0
    else:
        passed = True

    return {
        "check_type": "trust_score_consistency",
        "passed": passed,
        "details": {
            "trust_score": trust_score,
            "verdict": verdict
        },
        "server_id": server_id
    }


def verify_all_checks_for_server(server_id: str) -> List[Dict[str, Any]]:
    checks = [
        verify_signal_scores_populated,
        verify_risk_register_match,
        verify_trust_score_consistency,
    ]
    results = []
    for check_fn in checks:
        result = check_fn(server_id)
        results.append(result)
        log.debug(
            "Server %s | %s: %s",
            server_id,
            result["check_type"],
            "PASS" if result["passed"] else "FAIL"
        )
    return results


def persist_verification_result(result: Dict[str, Any]) -> None:
    import json
    verification_id = compute_idempotency_key(
        result["server_id"], result["check_type"]
    )
    row = {
        "verification_id": verification_id,
        "server_id": result["server_id"],
        "check_type": result["check_type"],
        "passed": result["passed"],
        "details_json": json.dumps(result.get("details", {})),
        "computed_at": utc_now_iso()
    }
    ws_write("score_push_verification_results", [row])


def cycle() -> Dict[str, Any]:
    log.info("Starting verification cycle")
    ensure_verification_results_table()

    servers = get_registry_servers(limit=50)
    total_servers = len(servers)
    total_checks = 0
    passed_checks = 0

    for server in servers:
        server_id = server.get("server_id")
        if not server_id:
            continue
        results = verify_all_checks_for_server(server_id)
        for result in results:
            persist_verification_result(result)
            total_checks += 1
            if result["passed"]:
                passed_checks += 1

    summary = {
        "servers_processed": total_servers,
        "total_checks": total_checks,
        "passed_checks": passed_checks,
        "failed_checks": total_checks - passed_checks,
        "pass_rate": round(passed_checks / total_checks, 4) if total_checks > 0 else 0.0
    }
    log.info(
        "Cycle complete: %d servers, %d checks, pass_rate=%.2f%%",
        total_servers, total_checks,
        (passed_checks / total_checks * 100) if total_checks > 0 else 0.0
    )
    return summary


def send_heartbeat(status: str = "running", meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "status": status,
        "last_heartbeat": utc_now_iso(),
        "meta": str(meta) if meta else "{}"
    }
    ws_write("service_health", [row])


def health() -> Dict[str, str]:
    return {
        "service": SERVICE_NAME,
        "status": "ok",
        "timestamp": utc_now_iso()
    }


def run() -> None:
    import signal
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info("%s starting", SERVICE_NAME)

    poll_interval = int(os.environ.get("POLL_SECS", 300))

    while True:
        try:
            summary = cycle()
            send_heartbeat("running", summary)
        except Exception as e:
            log.exception("Error in cycle: %s", e)
            send_heartbeat("error", {"error": str(e)})

        time.sleep(poll_interval)


if __name__ == "__main__":
    run()