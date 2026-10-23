import logging
import os
import sys
import time
import signal
from datetime import datetime, timezone
from pathlib import Path

import requests

SERVICE_NAME = "build_server_axis_deviation_analysis_contract"
SERVICE_PORT = None
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"
LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> dict:
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return {"rows": [], "error": str(e)}


def ws_write(table: str, rows: list) -> dict:
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={"table": table, "rows": rows, "wait": True}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return {"ok": False, "error": str(e)}


def ws_execute(sql: str) -> dict:
    try:
        resp = requests.post(EXECUTE_URL, json={"sql": sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return {"ok": False, "error": str(e)}


def check_single_instance():
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = pid_file.read_text().strip()
        log.warning(f"Another instance running with PID {old_pid}, exiting")
        sys.exit(0)
    pid_file.write_text(str(os.getpid()))


def remove_pid_file():
    Path(PID_FILE).unlink(missing_ok=True)


def signal_handler(signum, frame):
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(status: str = "running", meta: dict = None):
    row = {
        "service": SERVICE_NAME,
        "last_heartbeat": utc_now_iso(),
        "status": status,
        "meta": meta or {}
    }
    ws_write("service_health", [row])


def ensure_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS server_axis_deviation_analysis (
        analysis_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        axis_name VARCHAR NOT NULL,
        current_value DOUBLE,
        baseline_value DOUBLE,
        deviation_score DOUBLE,
        deviation_direction VARCHAR,
        percent_change DOUBLE,
        absolute_delta DOUBLE,
        z_score DOUBLE,
        p_value DOUBLE,
        is_significant BOOLEAN,
        confidence_level VARCHAR,
        window_start TIMESTAMPTZ,
        window_end TIMESTAMPTZ,
        analysis_timestamp TIMESTAMPTZ,
        metadata JSON
    )
    """
    ws_execute(sql)

    sql2 = """
    CREATE TABLE IF NOT EXISTS server_axis_baseline (
        baseline_id VARCHAR PRIMARY KEY,
        server_id VARCHAR NOT NULL,
        axis_name VARCHAR NOT NULL,
        baseline_value DOUBLE,
        baseline_mean DOUBLE,
        baseline_stddev DOUBLE,
        sample_size INTEGER,
        computed_at TIMESTAMPTZ,
        valid_from TIMESTAMPTZ,
        valid_until TIMESTAMPTZ,
        is_active BOOLEAN DEFAULT TRUE
    )
    """
    ws_execute(sql2)

    sql3 = """
    CREATE TABLE IF NOT EXISTS server_axis_threshold (
        threshold_id VARCHAR PRIMARY KEY,
        axis_name VARCHAR NOT NULL,
        alert_threshold DOUBLE,
        critical_threshold DOUBLE,
        warning_threshold DOUBLE,
        direction VARCHAR,
        updated_at TIMESTAMPTZ
    )
    """
    ws_execute(sql3)

    log.info("Axis deviation tables ensured")


def compute_deviation_id(server_id: str, axis_name: str, analysis_ts: str) -> str:
    import hashlib
    raw = f"{server_id}:{axis_name}:{analysis_ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_baseline_id(server_id: str, axis_name: str, valid_from: str) -> str:
    import hashlib
    raw = f"{server_id}:{axis_name}:{valid_from}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_server_ids_for_analysis(limit: int = 100) -> list:
    sql = f"""
    SELECT server_id FROM mcp_server_registry
    WHERE trust_score IS NOT NULL
    LIMIT {limit}
    """
    result = ws_query(sql)
    return [r.get("server_id") for r in result.get("rows", [])]


def get_server_time_series(server_id: str, axis_name: str, days_back: int = 30) -> list:
    start_date = datetime.now(timezone.utc)
    start_date = start_date.replace(day=max(1, start_date.day - days_back))
    start_iso = start_date.isoformat()

    if axis_name == "trust_score":
        table = "mcp_server_registry"
        col = "trust_score"
    elif axis_name == "scan_count":
        table = "mcp_server_registry"
        col = "scan_count"
    elif axis_name == "verdict":
        table = "mcp_server_registry"
        col = "verdict"
    else:
        table = "mcp_signal_scores"
        col = "score"

    if table == "mcp_signal_scores":
        sql = f"""
        SELECT scored_at as ts, score as value
        FROM mcp_signal_scores
        WHERE server_id = ? AND signal_name = ?
        AND scored_at >= ?
        ORDER BY scored_at ASC
        """
        result = ws_query(sql)
        rows = result.get("rows", [])
        return [{"ts": r.get("ts"), "value": r.get("value")} for r in rows]
    else:
        sql = f"""
        SELECT last_scanned as ts, {col} as value
        FROM {table}
        WHERE server_id = ? AND last_scanned >= ?
        ORDER BY last_scanned ASC
        """
        result = ws_query(sql)
        rows = result.get("rows", [])
        return [{"ts": r.get("ts"), "value": r.get("value")} for r in rows]


def compute_statistics(values: list) -> dict:
    if not values:
        return {"mean": 0, "stddev": 0, "min": 0, "max": 0, "count": 0}
    n = len(values)
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / n
    stddev = variance ** 0.5
    return {
        "mean": mean,
        "stddev": stddev if stddev > 0 else 0.0001,
        "min": min(values),
        "max": max(values),
        "count": n
    }


def compute_z_score(value: float, mean: float, stddev: float) -> float:
    if stddev == 0:
        return 0.0
    return (value - mean) / stddev


def estimate_p_value(z: float) -> float:
    import math
    abs_z = abs(z)
    p = 1.0 - (1.0 / (1.0 + 0.2316419 * abs_z)) * 0.319381530
    p = p * 0.398942280
    p = p * math.exp(-0.5 * abs_z * abs_z)
    p = 2.0 * p if z != 0 else 1.0
    return max(0.0, min(1.0, p))


def detect_deviation_direction(current: float, baseline: float) -> str:
    delta = current - baseline
    if abs(delta) < 0.001:
        return "stable"
    return "increasing" if delta > 0 else "decreasing"


def get_confidence_level(p_value: float) -> str:
    if p_value < 0.001:
        return "99.9%"
    elif p_value < 0.01:
        return "99%"
    elif p_value < 0.05:
        return "95%"
    elif p_value < 0.10:
        return "90%"
    else:
        return "ns"


def is_significant(p_value: float, alpha: float = 0.05) -> bool:
    return p_value < alpha


def get_axis_thresholds(axis_name: str) -> dict:
    sql = f"""
    SELECT * FROM server_axis_threshold
    WHERE axis_name = ? AND valid_until IS NULL
    """
    result = ws_query(sql)
    rows = result.get("rows", [])
    if rows:
        r = rows[0]
        return {
            "alert": r.get("alert_threshold"),
            "critical": r.get("critical_threshold"),
            "warning": r.get("warning_threshold"),
            "direction": r.get("direction")
        }
    defaults = {
        "trust_score": {"alert": 10.0, "critical": 20.0, "warning": 5.0, "direction": "any"},
        "scan_count": {"alert": 5.0, "critical": 10.0, "warning": 3.0, "direction": "any"},
        "verdict": {"alert": 0.5, "critical": 1.0, "warning": 0.3, "direction": "any"}
    }
    return defaults.get(axis_name, {"alert": 5.0, "critical": 10.0, "warning": 3.0, "direction": "any"})


def get_or_create_baseline(server_id: str, axis_name: str, lookback_days: int = 30) -> dict:
    sql = """
    SELECT * FROM server_axis_baseline
    WHERE server_id = ? AND axis_name = ? AND is_active = TRUE
    AND valid_until IS NULL
    ORDER BY computed_at DESC
    LIMIT 1
    """
    result = ws_query(sql)
    rows = result.get("rows", [])
    if rows:
        r = rows[0]
        valid_until = r.get("valid_until")
        if valid_until is None:
            return {
                "baseline_id": r.get("baseline_id"),
                "baseline_value": r.get("baseline_value"),
                "baseline_mean": r.get("baseline_mean"),
                "baseline_stddev": r.get("baseline_stddev"),
                "sample_size": r.get("sample_size"),
                "computed_at": r.get("computed_at")
            }

    time_series = get_server_time_series(server_id, axis_name, lookback_days)
    values = [ts["value"] for ts in time_series if ts.get("value") is not None]
    if not values:
        return None

    stats = compute_statistics(values)
    now_iso = utc_now_iso()
    baseline_id = compute_baseline_id(server_id, axis_name, now_iso)

    row = {
        "baseline_id": baseline_id,
        "server_id": server_id,
        "axis_name": axis_name,
        "baseline_value": stats["mean"],
        "baseline_mean": stats["mean"],
        "baseline_stddev": stats["stddev"],
        "sample_size": stats["count"],
        "computed_at": now_iso,
        "valid_from": now_iso,
        "valid_until": None,
        "is_active": True
    }
    ws_write("server_axis_baseline", [row])
    return {
        "baseline_id": baseline_id,
        "baseline_value": stats["mean"],
        "baseline_mean": stats["mean"],
        "baseline_stddev": stats["stddev"],
        "sample_size": stats["count"],
        "computed_at": now_iso
    }


def analyze_server_axis_deviation(server_id: str, axis_name: str, lookback_days: int = 30) -> dict:
    baseline = get_or_create_baseline(server_id, axis_name, lookback_days)
    if not baseline:
        return {"server_id": server_id, "axis_name": axis_name, "status": "no_baseline"}

    time_series = get_server_time_series(server_id, axis_name, lookback_days)
    if not time_series:
        return {"server_id": server_id, "axis_name": axis_name, "status": "no_data"}

    current_value = time_series[-1]["value"] if time_series else None
    if current_value is None:
        return {"server_id": server_id, "axis_name": axis_name, "status": "no_current_value"}

    baseline_mean = baseline["baseline_mean"]
    baseline_stddev = baseline["baseline_stddev"]

    z_score = compute_z_score(current_value, baseline_mean, baseline_stddev)
    p_value = estimate_p_value(z_score)
    deviation_score = abs(z_score)
    deviation_direction = detect_deviation_direction(current_value, baseline_mean)
    percent_change = ((current_value - baseline_mean) / baseline_mean * 100) if baseline_mean != 0 else 0.0
    absolute_delta = abs(current_value - baseline_mean)
    is_sig = is_significant(p_value)
    confidence = get_confidence_level(p_value)

    now_iso = utc_now_iso()
    analysis_id = compute_deviation_id(server_id, axis_name, now_iso)
    window_start = time_series[0]["ts"] if time_series else now_iso
    window_end = time_series[-1]["ts"] if time_series else now_iso

    thresholds = get_axis_thresholds(axis_name)

    row = {
        "analysis_id": analysis_id,
        "server_id": server_id,
        "axis_name": axis_name,
        "current_value": current_value,
        "baseline_value": baseline_mean,
        "deviation_score": deviation_score,
        "deviation_direction": deviation_direction,
        "percent_change": percent_change,
        "absolute_delta": absolute_delta,
        "z_score": z_score,
        "p_value": p_value,
        "is_significant": is_sig,
        "confidence_level": confidence,
        "window_start": window_start,
        "window_end": window_end,
        "analysis_timestamp": now_iso,
        "metadata": {
            "baseline_stddev": baseline_stddev,
            "sample_size": baseline["sample_size"],
            "alert_threshold": thresholds["alert"],
            "critical_threshold": thresholds["critical"]
        }
    }
    ws_write("server_axis_deviation_analysis", [row])
    return {"server_id": server_id, "axis_name": axis_name, "deviation_score": deviation_score, "is_significant": is_sig}


def get_all_axes() -> list:
    return [
        "trust_score",
        "scan_count",
        "verdict",
        "community_signal",
        "supply_chain",
        "temporal_stability",
        "permission_scope",
        "tool_description_safety"
    ]


def run_analysis_cycle(servers_per_axis: int = 50, axes: list = None):
    if axes is None:
        axes = get_all_axes()

    servers = get_server_ids_for_analysis(servers_per_axis * 2)
    servers = servers[:servers_per_axis]

    results = []
    for server_id in servers:
        for axis_name in axes:
            try:
                result = analyze_server_axis_deviation(server_id, axis_name)
                results.append(result)
            except Exception as e:
                log.error(f"Failed to analyze {server_id}/{axis_name}: {e}")

    significant_count = sum(1 for r in results if r.get("is_significant"))
    log.info(f"Analysis cycle complete: {len(results)} analyses, {significant_count} significant deviations")
    return results


def cycle():
    log.info("Starting axis deviation analysis cycle")
    results = run_analysis_cycle()
    return results


def run():
    log.info(f"Starting {SERVICE_NAME}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    ensure_tables()
    send_heartbeat("starting", {"phase": "init"})

    poll_interval = 300

    while True:
        try:
            cycle()
            send_heartbeat("running", {"last_cycle": utc_now_iso()})
        except Exception as e:
            log.error(f"Cycle error: {e}")
            send_heartbeat("error", {"error": str(e)})

        time.sleep(poll_interval)


if __name__ == "__main__":
    run()