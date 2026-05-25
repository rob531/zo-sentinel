import time
import signal
import logging
import requests
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta

SERVICE_NAME = "aidr_commit_enforcement"
PORT = 8786
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 30
POLL_SECS = 15
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

BLOCKED_VERDICTS = {"CAUTION_LIMITED", "HIGH_RISK_ISOLATED"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("/tmp/aidr_commit_enforcement.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(SERVICE_NAME)

_session = None


def get_session():
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": f"{SERVICE_NAME}/1.0", "Content-Type": "application/json"})
    return _session


def ws_write(table: str, rows: list) -> dict:
    payload = {"table": table, "rows": rows, "wait": True}
    resp = get_session().post(WRITE_SERVICE_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql: str) -> dict:
    payload = {"sql": sql}
    resp = get_session().post(QUERY_SERVICE_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict:
    payload = {"sql": sql}
    resp = get_session().post(EXECUTE_SERVICE_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat():
    try:
        ws_write("service_health", [{"service": SERVICE_NAME, "last_heartbeat": datetime.utcnow().isoformat()}])
    except Exception as e:
        log.warning("Heartbeat failed: %s", e)


def check_single_instance():
    import os, sys
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error("Another instance already running (PID %d). Exiting.", old_pid)
            sys.exit(1)
        except OSError:
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    log.info("PID file written: %s", PID_FILE)


def remove_pid_file():
    import os
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    remove_pid_file()
    log.info("Received signal %d, shutting down gracefully.", signum)
    raise SystemExit(0)


def lookup_server_verdict(server_id: str) -> dict:
    sql = f"SELECT server_id, verdict, trust_score FROM mcp_server_registry WHERE server_id = '{server_id}' LIMIT 1"
    result = ws_query(sql)
    rows = result.get("rows", [])
    if rows:
        return rows[0]
    return {}


def lookup_injection_resilience(server_id: str) -> float:
    sql = f"""SELECT score FROM mcp_signal_scores
              WHERE server_id = '{server_id}' AND signal_name = 'injection_resilience'
              ORDER BY scored_at DESC LIMIT 1"""
    result = ws_query(sql)
    rows = result.get("rows", [])
    if rows:
        try:
            return float(rows[0].get("score", 0))
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def lookup_verdict_expiry(server_id: str) -> datetime:
    sql = f"""SELECT scored_at FROM mcp_signal_scores
              WHERE server_id = '{server_id}'
              ORDER BY scored_at DESC LIMIT 1"""
    result = ws_query(sql)
    rows = result.get("rows", [])
    if rows:
        raw = rows[0].get("scored_at", "")
        if raw:
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                pass
    return None


VERDICT_EXPIRY_HOURS = 168


def is_verdict_stale(server_id: str, threshold_hours: int = VERDICT_EXPIRY_HOURS) -> bool:
    scored_at = lookup_verdict_expiry(server_id)
    if scored_at is None:
        return True
    now = datetime.utcnow()
    if scored_at.tzinfo is not None:
        now = now.replace(tzinfo=scored_at.tzinfo)
    age = now - scored_at
    return age > timedelta(hours=threshold_hours)


def check_verdict_gate(server_id: str, override: bool = False) -> dict:
    verdict_data = lookup_server_verdict(server_id)
    verdict = verdict_data.get("verdict", "UNKNOWN")
    trust_score = verdict_data.get("trust_score")

    if not verdict_data:
        return {
            "allowed": False,
            "reason": "SERVER_NOT_FOUND",
            "server_id": server_id,
            "verdict": None,
            "trust_score": None,
        }

    if verdict in BLOCKED_VERDICTS and not override:
        return {
            "allowed": False,
            "reason": f"VERDICT_BLOCKED:{verdict}",
            "server_id": server_id,
            "verdict": verdict,
            "trust_score": trust_score,
        }

    stale = is_verdict_stale(server_id)
    if stale and not override:
        return {
            "allowed": False,
            "reason": "VERDICT_STALE",
            "server_id": server_id,
            "verdict": verdict,
            "trust_score": trust_score,
            "stale_warning": True,
        }

    return {
        "allowed": True,
        "reason": "VERDICT_APPROVED",
        "server_id": server_id,
        "verdict": verdict,
        "trust_score": trust_score,
    }


def compute_injection_resilience_score(server_id: str) -> float:
    try:
        return lookup_injection_resilience(server_id)
    except Exception as e:
        log.warning("Failed to fetch injection_resilience for %s: %s", server_id, e)
        return 0.0


def build_commit_payload(server_id: str, commit_data: dict, override: bool = False) -> dict:
    gate_result = check_verdict_gate(server_id, override=override)
    if not gate_result["allowed"]:
        raise ValueError(f"Verdict gate denied: {gate_result['reason']}")

    inj_res_score = compute_injection_resilience_score(server_id)

    payload = dict(commit_data)
    payload["_sentinel_gate"] = {
        "passed": True,
        "verdict": gate_result["verdict"],
        "trust_score": gate_result["trust_score"],
        "injection_resilience_score": inj_res_score,
        "gate_server_id": server_id,
        "gate_checked_at": datetime.utcnow().isoformat(),
    }
    return payload


def record_gate_event(server_id: str, commit_sha: str, allowed: bool, reason: str, override: bool):
    try:
        ws_write("audit_log", [{
            "target_server_id": server_id,
            "event_type": "VERDICT_GATE",
            "actor": "aidr_commit_enforcement",
            "detail": json.dumps({
                "commit_sha": commit_sha,
                "allowed": allowed,
                "reason": reason,
                "override": override,
                "timestamp": datetime.utcnow().isoformat(),
            }),
            "created_at": datetime.utcnow().isoformat(),
        }])
    except Exception as e:
        log.warning("Failed to record gate event: %s", e)


def validate_commit_payload(payload: dict) -> bool:
    required = {"server_id", "commit_sha"}
    return all(k in payload for k in required)


def should_forward_commit(server_id: str, commit_sha: str, override: bool = False) -> dict:
    if not validate_commit_payload({"server_id": server_id, "commit_sha": commit_sha}):
        return {"forward": False, "error": "INVALID_PAYLOAD", "payload": None}

    gate = check_verdict_gate(server_id, override=override)
    if not gate["allowed"]:
        record_gate_event(server_id, commit_sha, False, gate["reason"], override)
        return {
            "forward": False,
            "error": gate["reason"],
            "server_id": server_id,
            "commit_sha": commit_sha,
            "verdict": gate["verdict"],
            "trust_score": gate["trust_score"],
            "payload": None,
        }

    try:
        enriched = build_commit_payload(server_id, {"server_id": server_id, "commit_sha": commit_sha}, override=override)
        record_gate_event(server_id, commit_sha, True, gate["reason"], override)
        return {
            "forward": True,
            "server_id": server_id,
            "commit_sha": commit_sha,
            "verdict": gate["verdict"],
            "trust_score": gate["trust_score"],
            "injection_resilience_score": enriched["_sentinel_gate"]["injection_resilience_score"],
            "payload": enriched,
        }
    except ValueError as e:
        return {"forward": False, "error": str(e), "payload": None}


def ensure_tables():
    tables = [
        """CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY,
            target_server_id VARCHAR,
            event_type VARCHAR,
            actor VARCHAR,
            detail VARCHAR,
            created_at VARCHAR
        )""",
    ]
    for sql in tables:
        try:
            ws_execute(sql)
        except Exception as e:
            log.debug("Table ensure: %s", e)


def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info("Starting %s daemon on port %d", SERVICE_NAME, PORT)

    try:
        ensure_tables()
    except Exception as e:
        log.warning("Table ensure failed on startup: %s", e)

    cycle_count = 0
    while True:
        try:
            send_heartbeat()
            cycle_count += 1
            if cycle_count % 4 == 0:
                log.info("Heartbeat cycle %d OK", cycle_count)
        except Exception as e:
            log.warning("Cycle %d heartbeat error: %s", cycle_count, e)
        time.sleep(POLL_SECS)


if __name__ == "__main__":
    run()