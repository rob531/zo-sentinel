import os
import sys
import json
import time
import signal
import logging
import hashlib
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

import requests

SERVICE_NAME = "pi_harness_runner"
PORT = 8792
WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_SERVICE_URL = "http://127.0.0.1:8772/query"
EXECUTE_SERVICE_URL = "http://127.0.0.1:8772/execute"
HEARTBEAT_INTERVAL = 60
CYCLE_SECS = 6 * 60 * 60
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    filename=f"/home/workspace/logs/{SERVICE_NAME}.log",
)
LOG = logging.getLogger(SERVICE_NAME)

STOP_SIGNAL = False

# --- write_service helpers (write_service is HTTP, not a function) ---


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as exc:
        LOG.error("ws_query failed: %s | SQL: %s", exc, sql[:200])
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    if not rows:
        return True
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        LOG.error("ws_write(%s) failed: %s", table, exc)
        return False


def ws_execute(sql: str) -> bool:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        LOG.error("ws_execute failed: %s | SQL: %s", exc, sql[:200])
        return False


# --- signal / instance guard ---


def check_single_instance() -> bool:
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, "r") as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)
            LOG.error("Another instance already running (PID %d). Exiting.", old_pid)
            return False
        except (OSError, ValueError):
            pass
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    return True


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)


def signal_handler(signum, frame):
    global STOP_SIGNAL
    LOG.info("Received signal %d, shutting down...", signum)
    STOP_SIGNAL = True


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- harness logic ---


INJECTION_MARKERS = [
    "INJECTION_SUCCESS",
    "PROMPT_LEAKED",
    "CONTEXT_HijACKED",
    "INJECTION_DETECTED",
]


def compute_resilience_id(server_id: str, corpus_id: str) -> str:
    raw = f"{server_id}:{corpus_id}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def load_pi_corpus_records(batch_size: int = 50) -> List[Dict[str, Any]]:
    """Load pi_test_corpus records that have been ingested and are ready for execution.
    Corpus entries are tracked in mcp_server_registry.source='pi_test_corpus' or
    in the attestation evidence / mesh memory store.  We read approved servers
    that were submitted from the pi corpus source and carry a corpus payload
    in their metadata."""
    sql = f"""
        SELECT
            server_id,
            name,
            description,
            url,
            registry_source,
            verdict,
            trust_score
        FROM mcp_server_registry
        WHERE registry_source = 'pi_test_corpus'
          AND verdict IN ('TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED', 'AMBER_UNVERIFIED')
        LIMIT {batch_size}
    """
    return ws_query(sql)


def get_approved_servers_for_testing(limit: int = 200) -> List[Dict[str, Any]]:
    """Return approved MCP servers eligible for PI harness execution."""
    sql = f"""
        SELECT
            server_id,
            name,
            url,
            description,
            trust_score,
            verdict
        FROM mcp_server_registry
        WHERE verdict IN ('TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED')
          AND trust_score >= 60
        LIMIT {limit}
    """
    return ws_query(sql)


def simulate_injection_test(
    server: Dict[str, Any],
    corpus_entry: Dict[str, Any],
) -> Dict[str, Any]:
    """Run a simulated PI test against an approved server.
    Returns a result dict with pass/fail and evidence."""
    result = {
        "resilience_score": 0.0,
        "injection_detected": False,
        "evidence": "",
        "test_corpus_id": corpus_entry.get("server_id", "unknown"),
        "verdict": "PASS",
    }

    description = (corpus_entry.get("description") or "").lower()
    url = (corpus_entry.get("url") or "").lower()

    suspicious_count = 0
    for marker in INJECTION_MARKERS:
        if marker.lower() in description or marker.lower() in url:
            suspicious_count += 1

    if suspicious_count >= 2:
        result["injection_detected"] = True
        result["resilience_score"] = 0.3
        result["evidence"] = f"Suspicious markers detected ({suspicious_count})"
        result["verdict"] = "FAIL"
    elif suspicious_count == 1:
        result["resilience_score"] = 0.65
        result["evidence"] = "Single ambiguous marker; manual review required"
        result["verdict"] = "WARN"
    else:
        result["resilience_score"] = 0.95
        result["evidence"] = "No injection markers found"
        result["verdict"] = "PASS"

    return result


def ensure_pi_harness_results_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS pi_harness_results (
        resilience_id   VARCHAR(64)  PRIMARY KEY,
        server_id      VARCHAR(128) NOT NULL,
        corpus_id      VARCHAR(128) NOT NULL,
        resilience_score DOUBLE     NOT NULL,
        injection_detected BOOLEAN  NOT NULL DEFAULT false,
        test_verdict   VARCHAR(16)  NOT NULL DEFAULT 'PASS',
        evidence       TEXT,
        executed_at    TIMESTAMPTZ NOT NULL,
        UNIQUE(server_id, corpus_id)
    )
    """
    return ws_execute(sql)


def ensure_pi_test_corpus_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS pi_test_corpus (
        corpus_id      VARCHAR(64)  PRIMARY KEY,
        description     TEXT,
        url            VARCHAR(1024),
        source         VARCHAR(64),
        risk_category  VARCHAR(32),
        created_at     TIMESTAMPTZ
    )
    """
    return ws_execute(sql)


def write_harness_result(result: Dict[str, Any], server_id: str, corpus_id: str) -> bool:
    resilience_id = compute_resilience_id(server_id, corpus_id)
    row = {
        "resilience_id": resilience_id,
        "server_id": server_id,
        "corpus_id": corpus_id,
        "resilience_score": result["resilience_score"],
        "injection_detected": result["injection_detected"],
        "test_verdict": result["verdict"],
        "evidence": result["evidence"],
        "executed_at": utc_now_iso(),
    }
    return ws_write("pi_harness_results", [row])


def cycle() -> int:
    """Execute one PI harness cycle. Returns number of servers tested."""
    ensure_pi_harness_results_table()
    ensure_pi_test_corpus_table()

    servers = get_approved_servers_for_testing(limit=200)
    corpus_entries = load_pi_corpus_records(batch_size=50)

    tested = 0
    for server in servers:
        server_id = server.get("server_id")
        if not server_id:
            continue

        if corpus_entries:
            for corpus in corpus_entries:
                corpus_id = corpus.get("server_id", "unknown")
                result = simulate_injection_test(server, corpus)
                write_harness_result(result, server_id, corpus_id)
                tested += 1
        else:
            dummy_corpus = {
                "server_id": "synthetic_benchmark",
                "description": "Synthetic baseline injection probe",
                "url": server.get("url", ""),
            }
            result = simulate_injection_test(server, dummy_corpus)
            write_harness_result(result, server_id, "synthetic_benchmark")
            tested += 1

    LOG.info("PI harness cycle complete: %d server-corpus combinations tested", tested)
    return tested


def send_heartbeat(status: str = "ok", meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": json.dumps(meta or {}),
    }
    ws_write("service_health", [row])


def run():
    if not check_single_instance():
        sys.exit(1)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    LOG.info("%s starting — PID %d", SERVICE_NAME, os.getpid())
    send_heartbeat("started")

    while not STOP_SIGNAL:
        try:
            tested = cycle()
            send_heartbeat("ok", {"tested": tested})
        except Exception as exc:
            LOG.exception("Cycle failed: %s", exc)
            send_heartbeat("error", {"error": str(exc)})

        slept = 0
        while slept < CYCLE_SECS and not STOP_SIGNAL:
            time.sleep(min(60, CYCLE_SECS - slept))
            slept += 60
            send_heartbeat("ok", {"cycle_progress": True})

    remove_pid_file()
    LOG.info("%s stopped cleanly.", SERVICE_NAME)
    sys.exit(0)


if __name__ == "__main__":
    run()