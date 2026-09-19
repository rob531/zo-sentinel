import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

SERVICE_NAME = "server_axis_profile_api"
SERVICE_PORT = int(os.environ.get("SERVICE_PORT", "8781"))
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE)],
)
logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_write failed for table {table}: {e}")
        return False


def ws_execute(sql: str, params: Optional[tuple] = None) -> bool:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json=payload,
            timeout=30,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_execute failed: {e}")
        return False


def check_single_instance() -> bool:
    if os.path.exists(PID_FILE):
        with open(PID_FILE, "r") as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f"Another instance is running with PID {old_pid}")
            return False
        except (OSError, ValueError):
            logger.warning(f"Stale PID file found: {old_pid}, removing")
            os.remove(PID_FILE)
    pid = os.getpid()
    with open(PID_FILE, "w") as f:
        f.write(str(pid))
    return True


def remove_pid_file() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except OSError as e:
        logger.warning(f"Failed to remove PID file: {e}")


def signal_handler(signum: int, frame: Any) -> None:
    logger.info(f"Received signal {signum}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def compute_profile_id(server_id: str, axis_name: str) -> str:
    import hashlib
    combined = f"{server_id}:{axis_name}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def get_server_profile(server_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        name,
        url,
        description,
        trust_score,
        verdict,
        registry_source,
        scan_count,
        first_seen,
        last_seen,
        last_scanned
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    rows = ws_query(sql, (server_id,))
    return rows[0] if rows else None


def get_server_signals(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        signal_name,
        score,
        evidence,
        scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    ORDER BY signal_name
    """
    return ws_query(sql, (server_id,))


def get_signal_scores_by_name(server_id: str, signal_name: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        signal_name,
        score,
        evidence,
        scored_at
    FROM mcp_signal_scores
    WHERE server_id = ? AND signal_name = ?
    ORDER BY scored_at DESC
    LIMIT 100
    """
    return ws_query(sql, (server_id, signal_name))


def get_server_risk_tier(server_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        risk_tier,
        risk_rank,
        threat_count,
        computed_at
    FROM mcp_risk_register
    WHERE server_id = ?
    """
    rows = ws_query(sql, (server_id,))
    return rows[0] if rows else None


def get_server_threat_associations(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        threat_type,
        severity,
        evidence,
        reported_at
    FROM mcp_threat_associations
    WHERE server_id = ?
    ORDER BY reported_at DESC
    """
    return ws_query(sql, (server_id,))


def get_server_attestations(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        server_id,
        attestation_type,
        attested_by,
        attested_at,
        expires_at,
        evidence_json,
        status
    FROM mcp_attestations
    WHERE server_id = ?
    ORDER BY attested_at DESC
    """
    return ws_query(sql, (server_id,))


def compute_axis_score(signals: List[Dict[str, Any]], axis_weights: Dict[str, float]) -> float:
    if not signals:
        return 0.0
    total_weight = 0.0
    weighted_sum = 0.0
    for sig in signals:
        signal_name = sig.get("signal_name", "")
        score = float(sig.get("score", 0) or 0)
        weight = axis_weights.get(signal_name, 0.5)
        weighted_sum += score * weight
        total_weight += weight
    if total_weight == 0:
        return 0.0
    return weighted_sum / total_weight


AXIS_WEIGHTS_TRUST = {
    "registry_source": 0.15,
    "domain_trust": 0.20,
    "community_signal": 0.15,
    "temporal_stability": 0.15,
    "permission_scope": 0.10,
    "tool_description_safety": 0.10,
    "supply_chain": 0.10,
    "injection_resilience": 0.05,
}


AXIS_WEIGHTS_SECURITY = {
    "permission_scope": 0.25,
    "tool_description_safety": 0.25,
    "injection_resilience": 0.20,
    "temporal_stability": 0.15,
    "supply_chain": 0.15,
}


AXIS_WEIGHTS_REPUTATION = {
    "community_signal": 0.30,
    "registry_source": 0.20,
    "domain_trust": 0.20,
    "supply_chain": 0.15,
    "temporal_stability": 0.15,
}


AXIS_PROFILES = {
    "trust": {"name": "Trust Profile", "weights": AXIS_WEIGHTS_TRUST},
    "security": {"name": "Security Profile", "weights": AXIS_WEIGHTS_SECURITY},
    "reputation": {"name": "Reputation Profile", "weights": AXIS_WEIGHTS_REPUTATION},
}


def compute_server_axis_profile(server_id: str, axis: str) -> Optional[Dict[str, Any]]:
    if axis not in AXIS_PROFILES:
        logger.warning(f"Unknown axis: {axis}")
        return None
    profile_info = AXIS_PROFILES[axis]
    weights = profile_info["weights"]
    signals = get_server_signals(server_id)
    if not signals:
        return None
    axis_score = compute_axis_score(signals, weights)
    signal_breakdown = {}
    for sig in signals:
        sn = sig.get("signal_name", "")
        if sn in weights:
            signal_breakdown[sn] = {
                "score": float(sig.get("score", 0) or 0),
                "weight": weights[sn],
                "contribution": float(sig.get("score", 0) or 0) * weights[sn],
                "evidence": sig.get("evidence", ""),
            }
    return {
        "server_id": server_id,
        "axis": axis,
        "axis_name": profile_info["name"],
        "composite_score": axis_score,
        "signal_breakdown": signal_breakdown,
        "computed_at": utc_now_iso(),
    }


def get_server_full_profile(server_id: str) -> Dict[str, Any]:
    profile = get_server_profile(server_id)
    if not profile:
        return {}
    risk_tier = get_server_risk_tier(server_id)
    threats = get_server_threat_associations(server_id)
    attestations = get_server_attestations(server_id)
    signals = get_server_signals(server_id)
    axis_profiles = {}
    for axis_name in AXIS_PROFILES:
        axis_result = compute_server_axis_profile(server_id, axis_name)
        if axis_result:
            axis_profiles[axis_name] = axis_result
    return {
        "server": profile,
        "risk_tier": risk_tier,
        "threats": threats,
        "attestations": attestations,
        "signals": signals,
        "axis_profiles": axis_profiles,
        "profile_computed_at": utc_now_iso(),
    }


def get_servers_by_axis_profile(
    axis: str,
    min_score: float = 0.0,
    max_score: float = 1.0,
    limit: int = 100,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    if axis not in AXIS_PROFILES:
        return []
    weights = AXIS_PROFILES[axis]["weights"]
    signal_names = list(weights.keys())
    placeholders = ", ".join(["?" for _ in signal_names])
    sql = f"""
    SELECT 
        r.server_id,
        r.name,
        r.url,
        r.trust_score,
        r.verdict,
        r.risk_tier
    FROM mcp_server_registry r
    WHERE r.server_id IN (
        SELECT DISTINCT server_id FROM mcp_signal_scores WHERE signal_name IN ({placeholders})
    )
    ORDER BY r.trust_score DESC
    LIMIT ? OFFSET ?
    """
    params = tuple(signal_names) + (limit, offset)
    servers = ws_query(sql, params)
    results = []
    for server in servers:
        sid = server.get("server_id", "")
        profile = compute_server_axis_profile(sid, axis)
        if profile and min_score <= profile.get("composite_score", 0) <= max_score:
            results.append(
                {
                    "server": server,
                    "axis_profile": profile,
                }
            )
    return results


def save_server_axis_profile(
    server_id: str,
    axis: str,
    composite_score: float,
    signal_breakdown: Dict[str, Any],
    evidence_json: str,
) -> bool:
    profile_id = compute_profile_id(server_id, axis)
    now = utc_now_iso()
    sql = """
    INSERT INTO server_axis_profiles (profile_id, server_id, axis, composite_score, signal_breakdown_json, computed_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT (server_id, axis) DO UPDATE SET
        composite_score = excluded.composite_score,
        signal_breakdown_json = excluded.signal_breakdown_json,
        computed_at = excluded.computed_at,
        updated_at = excluded.updated_at
    """
    import json

    evidence_str = json.dumps(signal_breakdown) if isinstance(signal_breakdown, dict) else evidence_json
    params = (profile_id, server_id, axis, composite_score, evidence_str, now, now)
    return ws_execute(sql, params)


def ensure_axis_profiles_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS server_axis_profiles (
        profile_id VARCHAR(64) PRIMARY KEY,
        server_id VARCHAR(128) NOT NULL,
        axis VARCHAR(64) NOT NULL,
        composite_score DOUBLE,
        signal_breakdown_json TEXT,
        computed_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ,
        UNIQUE (server_id, axis)
    )
    """
    return ws_execute(sql)


def get_axis_profile_history(
    server_id: str,
    axis: str,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    sql = """
    SELECT 
        profile_id,
        server_id,
        axis,
        composite_score,
        computed_at,
        updated_at
    FROM server_axis_profiles
    WHERE server_id = ? AND axis = ?
    ORDER BY computed_at DESC
    LIMIT ?
    """
    return ws_query(sql, (server_id, axis, limit))


def get_axis_trend(
    server_id: str,
    axis: str,
    days: int = 30,
) -> Dict[str, Any]:
    import datetime

    cutoff = datetime.datetime.now(timezone.utc) - datetime.timedelta(days=days)
    sql = """
    SELECT 
        computed_at,
        composite_score
    FROM server_axis_profiles
    WHERE server_id = ? AND axis = ? AND computed_at >= ?
    ORDER BY computed_at ASC
    """
    history = ws_query(sql, (server_id, axis, cutoff.isoformat()))
    if not history:
        return {"trend": "unknown", "data_points": 0, "history": []}
    scores = [h.get("composite_score", 0) for h in history]
    first_score = scores[0] if scores else 0
    last_score = scores[-1] if scores else 0
    delta = last_score - first_score
    if delta > 0.1:
        trend = "improving"
    elif delta < -0.1:
        trend = "declining"
    else:
        trend = "stable"
    return {
        "trend": trend,
        "delta": delta,
        "first_score": first_score,
        "last_score": last_score,
        "data_points": len(scores),
        "history": history,
    }


def get_top_servers_by_axis(axis: str, limit: int = 50) -> List[Dict[str, Any]]:
    if axis not in AXIS_PROFILES:
        return []
    weights = AXIS_PROFILES[axis]["weights"]
    signal_names = list(weights.keys())
    placeholders = ", ".join(["?" for _ in signal_names])
    sql = f"""
    SELECT 
        r.server_id,
        r.name,
        r.url,
        r.trust_score,
        r.verdict,
        r.registry_source
    FROM mcp_server_registry r
    WHERE r.server_id IN (
        SELECT DISTINCT server_id 
        FROM mcp_signal_scores 
        WHERE signal_name IN ({placeholders})
    )
    AND r.verdict != 'UNKNOWN'
    ORDER BY r.trust_score DESC
    LIMIT ?
    """
    params = tuple(signal_names) + (limit,)
    return ws_query(sql, params)


def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "timestamp": utc_now_iso(),
    }


def send_heartbeat() -> None:
    payload = {
        "service": SERVICE_NAME,
        "status": "running",
        "ts": utc_now_iso(),
        "meta": {"pid": os.getpid()},
    }
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": "service_health", "rows": [payload], "wait": True},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Heartbeat failed: {e}")


def run() -> None:
    import signal

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    if not check_single_instance():
        sys.exit(1)
    logger.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    ensure_axis_profiles_table()
    cycle_count = 0
    while True:
        try:
            cycle(cycle_count)
            cycle_count += 1
            send_heartbeat()
        except Exception as e:
            logger.error(f"Error in cycle {cycle_count}: {e}")
        import time

        time.sleep(60)


def cycle(cycle_num: int) -> None:
    logger.debug(f"Cycle {cycle_num} executed")


if __name__ == "__main__":
    run()