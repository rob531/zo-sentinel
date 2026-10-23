import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SERVICE_NAME = "investigate_known_bad_pattern_signal_discrimination_v2"
PORT = None  # one-shot diagnostic, no HTTP port
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = WRITE_SERVICE_URL + "/query"
EXECUTE_SERVICE_URL = WRITE_SERVICE_URL + "/execute"
WRITE_SERVICE_API_URL = WRITE_SERVICE_URL + "/write"

# ---------------------------------------------------------------------------
# Logging (FileHandler only – no StreamHandler, no basicConfig duplication)
# ---------------------------------------------------------------------------
logger = logging.getLogger(SERVICE_NAME)
_handler = logging.FileHandler(LOG_FILE)
_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
)
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Single-instance guard helpers
# ---------------------------------------------------------------------------
def check_single_instance() -> None:
    pid = str(os.getpid())
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old = pid_path.read_text().strip()
        if old != pid:
            try:
                os.kill(int(old), 0)
                logger.error("Another instance already running (PID %s). Exiting.", old)
                sys.exit(1)
            except (OSError, ValueError):
                logger.warning("Stale PID file %s (process %s is gone). Overwriting.", PID_FILE, old)
    pid_path.write_text(pid)


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum: int, frame) -> None:
    logger.info("Received signal %d – shutting down gracefully.", signum)
    remove_pid_file()
    sys.exit(0)


# ---------------------------------------------------------------------------
# Write-service helpers
# ---------------------------------------------------------------------------
def ws_query(sql: str) -> list[dict[str, Any]]:
    """SELECT via write_service /query endpoint."""
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("rows", [])
        logger.warning("ws_query returned %d: %s", resp.status_code, sql[:200])
    except Exception as e:
        logger.error("ws_query failed: %s", e)
    return []


def ws_write(table: str, rows: list[dict[str, Any]]) -> bool:
    """INSERT/UPSERT via write_service /write endpoint."""
    if not rows:
        return True
    try:
        resp = requests.post(
            WRITE_SERVICE_API_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        ok = resp.status_code == 200 and resp.json().get("ok", False)
        if not ok:
            logger.warning("ws_write to %s returned %s: %s", table, resp.status_code, resp.text[:200])
        return ok
    except Exception as e:
        logger.error("ws_write to %s failed: %s", table, e)
        return False


def ws_execute(sql: str) -> bool:
    """DDL/DML via write_service /execute endpoint."""
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql, "wait": True},
            timeout=30,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.error("ws_execute failed: %s", e)
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------
def send_heartbeat(status: str = "running", meta: str = "") -> None:
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "ts": utc_now_iso(),
        "meta": meta,
    }])


# ---------------------------------------------------------------------------
# Core investigation
# ---------------------------------------------------------------------------

def get_known_bad_pattern_distribution() -> list[dict[str, Any]]:
    """Return distinct score distribution for known_bad_pattern across all servers."""
    sql = """
    SELECT
        score,
        COUNT(*)                   AS total_rows,
        COUNT(DISTINCT server_id)  AS distinct_servers
    FROM mcp_signal_scores
    WHERE signal_name = 'known_bad_pattern'
    GROUP BY score
    ORDER BY score DESC
    """
    return ws_query(sql)


def get_sample_servers_by_score(score: float) -> list[dict[str, Any]]:
    """Return up to 5 servers with their names for a given score value."""
    sql = f"""
    SELECT
        s.server_id,
        r.name,
        r.verdict,
        r.trust_score,
        s.score,
        s.evidence,
        s.scored_at
    FROM mcp_signal_scores s
    LEFT JOIN mcp_server_registry r
           ON r.server_id = s.server_id
    WHERE s.signal_name = 'known_bad_pattern'
      AND s.score = {score}
    LIMIT 5
    """
    return ws_query(sql)


def get_high_score_servers_without_signal() -> list[dict[str, Any]]:
    """Find servers that are high-risk / known_threat but lack known_bad_pattern score."""
    sql = """
    SELECT
        r.server_id,
        r.name,
        r.verdict,
        r.trust_score,
        r.risk_tier
    FROM mcp_server_registry r
    WHERE r.verdict IN ('known_threat', 'untrusted')
      AND NOT EXISTS (
          SELECT 1 FROM mcp_signal_scores s
           WHERE s.server_id = r.server_id
             AND s.signal_name = 'known_bad_pattern'
      )
    LIMIT 20
    """
    return ws_query(sql)


def get_threat_associations_for_servers(server_ids: list[str]) -> list[dict[str, Any]]:
    """Look up threat associations for a batch of server IDs."""
    if not server_ids:
        return []
    # Parameterise via IN-list – safe because server_ids come from mcp_signal_scores
    quoted = ", ".join(f"'{sid}'" for sid in server_ids)
    sql = f"""
    SELECT
        server_id,
        threat_type,
        severity,
        reported_at
    FROM mcp_threat_associations
    WHERE server_id IN ({quoted})
    ORDER BY reported_at DESC
    """
    return ws_query(sql)


def get_all_signal_scores_for_server(server_id: str) -> list[dict[str, Any]]:
    """Fetch all signals for a specific server (used to detect missing dimensions)."""
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


def get_distinct_signal_names() -> list[dict[str, Any]]:
    """Return all signal types in the registry and how many servers have each."""
    sql = """
    SELECT
        signal_name,
        COUNT(DISTINCT server_id) AS servers_scored,
        COUNT(*)                   AS total_rows,
        MIN(score)                 AS min_score,
        MAX(score)                 AS max_score,
        COUNT(DISTINCT score)      AS distinct_scores
    FROM mcp_signal_scores
    GROUP BY signal_name
    ORDER BY signal_name
    """
    return ws_query(sql)


def compute_discrimination_entropy(distribution: list[dict[str, Any]]) -> float:
    """
    Compute Shannon entropy of the score distribution.
    Higher entropy means better discrimination (scores spread across values).
    """
    total = sum(row["total_rows"] for row in distribution)
    if total == 0:
        return 0.0
    import math
    entropy = 0.0
    for row in distribution:
        p = row["total_rows"] / total
        if p > 0:
            entropy -= p * math.log2(p)
    return round(entropy, 4)


# ---------------------------------------------------------------------------
# Diagnostic output
# ---------------------------------------------------------------------------

def run_investigation() -> dict[str, Any]:
    """Run the full investigation and return a summary dict."""
    logger.info("=== Starting known_bad_pattern signal discrimination investigation ===")
    result: dict[str, Any] = {
        "investigated_at": utc_now_iso(),
        "signal": "known_bad_pattern",
    }

    # 1. Distribution of known_bad_pattern scores
    distribution = get_known_bad_pattern_distribution()
    result["distribution"] = distribution
    distinct_scores = len(distribution)
    result["distinct_score_count"] = distinct_scores
    logger.info("Distinct score values for known_bad_pattern: %d", distinct_scores)
    for row in distribution:
        logger.info("  score=%s  rows=%d  servers=%d",
                    row.get("score"), row.get("total_rows"), row.get("distinct_servers"))

    # 2. Shannon entropy as discrimination metric
    entropy = compute_discrimination_entropy(distribution)
    result["entropy"] = entropy
    logger.info("Shannon entropy: %.4f (max for 2 values ≈ 1.0)", entropy)
    result["entropy_interpretation"] = (
        "HIGH discrimination" if entropy >= 1.0
        else "MODERATE discrimination" if entropy >= 0.5
        else "LOW discrimination – scores clustered"
    )

    # 3. Sample servers at each score value
    sample_by_score: dict[float, list] = {}
    for row in distribution:
        score = float(row["score"])
        samples = get_sample_servers_by_score(score)
        sample_by_score[score] = samples
        logger.info("  Samples at score %.1f:", score)
        for s in samples:
            logger.info("    server_id=%s  name=%s  verdict=%s",
                        s.get("server_id"), s.get("name"), s.get("verdict"))
    result["sample_servers_by_score"] = sample_by_score

    # 4. Known-threat servers missing the signal (potential false negatives)
    missing = get_high_score_servers_without_signal()
    result["high_risk_servers_missing_signal"] = missing
    logger.info("High-risk servers WITHOUT known_bad_pattern score: %d", len(missing))
    for srv in missing[:5]:
        logger.info("  server_id=%s  verdict=%s", srv.get("server_id"), srv.get("verdict"))

    # 5. Deep-dive on a sample high-risk server to see full signal picture
    if missing:
        sample_server_id = missing[0]["server_id"]
        full_signals = get_all_signal_scores_for_server(sample_server_id)
        result["sample_server_full_signals"] = {
            "server_id": sample_server_id,
            "signals": full_signals,
        }
        logger.info("Full signal profile for %s:", sample_server_id)
        for sig in full_signals:
            logger.info("  %s score=%.2f", sig.get("signal_name"), sig.get("score"))

    # 6. Compare discrimination against all other signals
    all_signals = get_distinct_signal_names()
    result["all_signal_discrimination"] = all_signals
    known_bad = next(
        (s for s in all_signals if s["signal_name"] == "known_bad_pattern"), None
    )
    if known_bad:
        result["known_bad_pattern_stats"] = known_bad
    logger.info("All signal discrimination summary:")
    for sig in all_signals:
        logger.info("  %-40s servers=%5d  distinct_scores=%2d  min=%.2f  max=%.2f",
                    sig["signal_name"],
                    sig["servers_scored"],
                    sig["distinct_scores"],
                    sig["min_score"] or 0,
                    sig["max_score"] or 0)

    # 7. Diagnosis verdict
    if distinct_scores <= 2 and entropy < 0.8:
        result["diagnosis"] = (
            "POOR discrimination – known_bad_pattern has only %d distinct score(s) "
            "and entropy %.4f. Likely cause: signal is produced by a threshold that "
            "rarely fires, or downstream normalisation collapses many raw values "
            "into one or two buckets. Check: (a) raw source in signal_analyser or "
            "trust_synthesiser; (b) whether evidence blob varies at all; "
            "(c) whether mcp_threat_associations table has entries that should be "
            "driving this signal but are not being JOINed."
        ) % (distinct_scores, entropy)
        logger.warning("DIAGNOSIS: %s", result["diagnosis"])
    else:
        result["diagnosis"] = (
            f"Discrimination is acceptable: {distinct_scores} distinct scores, "
            f"entropy {entropy:.4f}. No immediate concern."
        )
        logger.info("DIAGNOSIS: %s", result["diagnosis"])

    logger.info("=== Investigation complete ===")
    return result


# ---------------------------------------------------------------------------
# Persist findings
# ---------------------------------------------------------------------------

def ensure_diagnostic_table() -> None:
    """Create the diagnostic results table if it does not exist."""
    sql = """
    CREATE TABLE IF NOT EXISTS investigate_known_bad_pattern_diagnostics (
        investigated_at     TIMESTAMPTZ,
        signal              VARCHAR(128),
        distinct_score_count INTEGER,
        entropy              DOUBLE,
        entropy_interpretation VARCHAR(64),
        diagnosis           TEXT,
        distribution_json    TEXT,
        all_signals_json     TEXT,
        PRIMARY KEY (investigated_at, signal)
    )
    """
    ws_execute(sql)


def persist_results(result: dict[str, Any]) -> None:
    """Write investigation summary to a diagnostic table."""
    ensure_diagnostic_table()
    sql = """
    INSERT INTO investigate_known_bad_pattern_diagnostics
        (investigated_at, signal, distinct_score_count, entropy,
         entropy_interpretation, diagnosis,
         distribution_json, all_signals_json)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    payload = {
        "sql": sql,
        "params": [
            result["investigated_at"],
            result["signal"],
            result["distinct_score_count"],
            result["entropy"],
            result["entropy_interpretation"],
            result["diagnosis"],
            json.dumps(result.get("distribution", [])),
            json.dumps(result.get("all_signal_discrimination", [])),
        ],
    }
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            logger.info("Diagnostic results persisted.")
        else:
            logger.warning("Failed to persist results: %s", resp.text[:200])
    except Exception as e:
        logger.error("persist_results failed: %s", e)


# ---------------------------------------------------------------------------
# Daemon entry-points (one-shot, exits on completion)
# ---------------------------------------------------------------------------

def cycle() -> dict[str, Any]:
    """One unit of work: run investigation and persist results."""
    result = run_investigation()
    persist_results(result)
    send_heartbeat(status="complete", meta=f"distinct_scores={result['distinct_score_count']}")
    return result


def run() -> None:
    """Loop wrapper (kept for interface compatibility; one shot exits immediately)."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    logger.info("Starting %s", SERVICE_NAME)
    send_heartbeat(status="starting")

    try:
        result = cycle()
        logger.info("Cycle complete. Diagnosis: %s", result.get("diagnosis", "n/a")[:120])
    except Exception as e:
        logger.exception("Investigation failed: %s", e)
        send_heartbeat(status="error", meta=str(e))
    finally:
        remove_pid_file()
        logger.info("Exiting %s", SERVICE_NAME)
        sys.exit(0)


if __name__ == "__main__":
    run()