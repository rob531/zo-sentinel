import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SERVICE_NAME = "build_server_axis_deviation_analysis_logic"
PORT = 0  # Logic module, not a listening service
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stderr),
    ],
)
log = logging.getLogger(SERVICE_NAME)

# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service /query endpoint."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        log.error("ws_query failed: %s | SQL: %s", e, sql[:200])
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write rows to DuckDB via write_service /write endpoint."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write failed: %s | table: %s", e, table)
        return False


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    """Execute DDL/DML via write_service /execute endpoint."""
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            f"{EXECUTE_SERVICE_URL}/execute",
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_execute failed: %s | SQL: %s", e, sql[:200])
        return False


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Axis Deviation Analysis Logic
# ---------------------------------------------------------------------------


def compute_z_score(value: float, mean: float, std_dev: float) -> float:
    """Compute z-score for a value given distribution parameters."""
    if std_dev == 0:
        return 0.0
    return (value - mean) / std_dev


def compute_expected_distribution(
    servers: List[Dict[str, Any]], dimension: str
) -> Tuple[float, float, Dict[str, int]]:
    """
    Compute expected distribution parameters for a given dimension.
    Returns (mean, std_dev, tier_counts).
    """
    if not servers:
        return 0.0, 0.0, {}

    values = []
    tier_counts: Dict[str, int] = {}

    for server in servers:
        tier = server.get("risk_tier", "UNKNOWN")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        # Map risk_tier to numeric score for statistical analysis
        tier_scores = {
            "CRITICAL": 100,
            "HIGH": 75,
            "MEDIUM": 50,
            "LOW": 25,
            "MINIMAL": 10,
            "UNKNOWN": 50,
        }
        values.append(tier_scores.get(tier, 50))

    if not values:
        return 0.0, 0.0, tier_counts

    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    std_dev = variance ** 0.5

    return mean, std_dev, tier_counts


def compute_axis_deviation_score(
    server: Dict[str, Any],
    expected_mean: float,
    expected_std: float,
    dimension: str,
) -> float:
    """
    Compute axis deviation score for a single server.
    Returns a score from 0-100 where higher = more deviant from expected.
    """
    tier_scores = {
        "CRITICAL": 100,
        "HIGH": 75,
        "MEDIUM": 50,
        "LOW": 25,
        "MINIMAL": 10,
        "UNKNOWN": 50,
    }

    server_tier = server.get("risk_tier", "UNKNOWN")
    server_score = tier_scores.get(server_tier, 50)

    z_score = compute_z_score(server_score, expected_mean, expected_std)

    # Convert z-score to deviation score (0-100 scale)
    # z-score of 0 = no deviation (score 0)
    # z-score of 2+ or -2- = high deviation (score 100)
    deviation_score = min(100, abs(z_score) * 40)

    return round(deviation_score, 2)


def analyze_signal_deviation(
    server_id: str, dimension: str = "trust_score"
) -> Dict[str, Any]:
    """
    Analyze signal-level deviation for a specific server.
    Returns deviation metrics and anomaly flags.
    """
    result: Dict[str, Any] = {
        "server_id": server_id,
        "dimension": dimension,
        "deviation_score": 0.0,
        "z_score": 0.0,
        "is_anomaly": False,
        "anomaly_reasons": [],
        "timestamp": utc_now_iso(),
    }

    # Fetch signal scores for this server
    sql = """
        SELECT signal_name, score, evidence
        FROM mcp_signal_scores
        WHERE server_id = ?
        ORDER BY signal_name
    """
    signals = ws_query(sql, [server_id])

    if not signals:
        result["anomaly_reasons"].append("No signal scores found for server")
        result["is_anomaly"] = True
        return result

    # Fetch all servers' scores for the same dimension for baseline
    baseline_sql = """
        SELECT server_id, score
        FROM mcp_signal_scores
        WHERE signal_name = ?
    """
    baseline = ws_query(baseline_sql, [dimension])

    if not baseline:
        result["anomaly_reasons"].append("No baseline data for dimension")
        return result

    scores = [s["score"] for s in baseline if s.get("score") is not None]
    if not scores:
        result["anomaly_reasons"].append("No valid baseline scores")
        return result

    mean = sum(scores) / len(scores)
    variance = sum((s - mean) ** 2 for s in scores) / len(scores)
    std_dev = variance ** 0.5

    # Find this server's score
    server_signal = next(
        (s for s in signals if s.get("signal_name") == dimension), None
    )
    if server_signal and server_signal.get("score") is not None:
        server_score = server_signal["score"]
        z_score = compute_z_score(server_score, mean, std_dev)
        deviation_score = min(100, abs(z_score) * 40)

        result["z_score"] = round(z_score, 3)
        result["deviation_score"] = round(deviation_score, 2)
        result["baseline_mean"] = round(mean, 2)
        result["baseline_std_dev"] = round(std_dev, 2)

        # Flag anomalies
        if abs(z_score) > 2.0:
            result["is_anomaly"] = True
            result["anomaly_reasons"].append(
                f"Z-score {z_score:.2f} exceeds threshold of 2.0"
            )
        if deviation_score > 70:
            result["is_anomaly"] = True
            result["anomaly_reasons"].append(
                f"Deviation score {deviation_score} indicates significant deviation"
            )

    return result


def identify_axis_outliers(
    limit: int = 50, threshold: float = 60.0
) -> List[Dict[str, Any]]:
    """
    Identify servers that are outliers on risk axis.
    Returns list of servers with high deviation scores.
    """
    # Get all servers with risk tiers
    sql = """
        SELECT
            r.server_id,
            r.name,
            r.risk_tier,
            r.trust_score,
            r.verdict,
            r.last_assessed
        FROM mcp_server_registry r
        WHERE r.risk_tier IS NOT NULL
        ORDER BY r.last_assessed DESC
        LIMIT 1000
    """
    servers = ws_query(sql)

    if not servers:
        log.warning("No servers found for axis outlier analysis")
        return []

    # Compute expected distribution
    expected_mean, expected_std, _ = compute_expected_distribution(servers, "risk_tier")

    # Score each server
    outliers = []
    for server in servers:
        deviation_score = compute_axis_deviation_score(
            server, expected_mean, expected_std, "risk_tier"
        )
        if deviation_score >= threshold:
            outliers.append(
                {
                    "server_id": server["server_id"],
                    "name": server.get("name", "Unknown"),
                    "risk_tier": server.get("risk_tier", "UNKNOWN"),
                    "deviation_score": deviation_score,
                    "trust_score": server.get("trust_score"),
                    "verdict": server.get("verdict"),
                    "last_assessed": server.get("last_assessed"),
                    "timestamp": utc_now_iso(),
                }
            )

    # Sort by deviation score descending
    outliers.sort(key=lambda x: x["deviation_score"], reverse=True)
    return outliers[:limit]


def compute_tier_distribution_deviation(
    observed_counts: Dict[str, int],
    expected_counts: Dict[str, int],
) -> Dict[str, Any]:
    """
    Compute deviation between observed and expected tier distributions.
    Returns deviation metrics and Chi-square-like statistics.
    """
    result: Dict[str, Any] = {
        "observed": observed_counts,
        "expected": expected_counts,
        "deviations": {},
        "total_deviation": 0.0,
        "max_tier_deviation": 0.0,
        "most_deviated_tier": None,
        "timestamp": utc_now_iso(),
    }

    total_observed = sum(observed_counts.values())
    total_expected = sum(expected_counts.values())

    if total_observed == 0 or total_expected == 0:
        return result

    for tier in set(list(observed_counts.keys()) + list(expected_counts.keys())):
        obs = observed_counts.get(tier, 0)
        exp = expected_counts.get(tier, 0)

        obs_pct = (obs / total_observed) * 100
        exp_pct = (exp / total_expected) * 100

        deviation = obs_pct - exp_pct
        abs_deviation = abs(deviation)

        result["deviations"][tier] = {
            "observed_count": obs,
            "expected_count": exp,
            "observed_pct": round(obs_pct, 2),
            "expected_pct": round(exp_pct, 2),
            "deviation_pct": round(deviation, 2),
            "abs_deviation_pct": round(abs_deviation, 2),
        }

        result["total_deviation"] += abs_deviation
        if abs_deviation > result["max_tier_deviation"]:
            result["max_tier_deviation"] = round(abs_deviation, 2)
            result["most_deviated_tier"] = tier

    result["total_deviation"] = round(result["total_deviation"], 2)

    return result


def analyze_risk_tier_axis() -> Dict[str, Any]:
    """
    Analyze the overall risk tier axis for registry.
    Returns distribution analysis and deviation from expected baseline.
    """
    # Get current tier distribution
    sql = """
        SELECT risk_tier, COUNT(*) as count
        FROM mcp_server_registry
        WHERE risk_tier IS NOT NULL
        GROUP BY risk_tier
        ORDER BY count DESC
    """
    current_dist = ws_query(sql)

    observed_counts: Dict[str, int] = {}
    for row in current_dist:
        tier = row.get("risk_tier", "UNKNOWN")
        observed_counts[tier] = row.get("count", 0)

    # Expected distribution (based on security best practices)
    # Most servers should be in MEDIUM-LOW range
    total = sum(observed_counts.values())
    expected_counts = {
        "LOW": int(total * 0.35),
        "MEDIUM": int(total * 0.30),
        "HIGH": int(total * 0.15),
        "CRITICAL": int(total * 0.05),
        "MINIMAL": int(total * 0.10),
        "UNKNOWN": int(total * 0.05),
    }

    deviation_analysis = compute_tier_distribution_deviation(
        observed_counts, expected_counts
    )

    return {
        "total_servers": total,
        "observed_distribution": observed_counts,
        "expected_distribution": expected_counts,
        "deviation_analysis": deviation_analysis,
        "timestamp": utc_now_iso(),
    }


def generate_deviation_alerts(
    threshold: float = 70.0, limit: int = 20
) -> List[Dict[str, Any]]:
    """
    Generate alerts for servers with significant axis deviation.
    Returns list of alert objects.
    """
    outliers = identify_axis_outliers(limit=limit, threshold=threshold)

    alerts = []
    for outlier in outliers:
        severity_map = {
            90: "CRITICAL",
            80: "HIGH",
            70: "MEDIUM",
        }
        severity = "LOW"
        for score, sev in sorted(severity_map.items(), reverse=True):
            if outlier["deviation_score"] >= score:
                severity = sev
                break

        alert = {
            "alert_id": f"AXIS_DEV_{outlier['server_id'][:8]}_{int(time.time())}",
            "server_id": outlier["server_id"],
            "server_name": outlier.get("name", "Unknown"),
            "severity": severity,
            "deviation_score": outlier["deviation_score"],
            "current_tier": outlier.get("risk_tier"),
            "trust_score": outlier.get("trust_score"),
            "verdict": outlier.get("verdict"),
            "reason": f"Server deviates from expected risk tier distribution (score: {outlier['deviation_score']})",
            "timestamp": utc_now_iso(),
        }
        alerts.append(alert)

    return alerts


def compute_axis_stability_score(
    server_id: str, lookback_days: int = 30
) -> Dict[str, Any]:
    """
    Compute axis stability score for a server over time.
    Returns stability metrics indicating consistency of risk positioning.
    """
    result: Dict[str, Any] = {
        "server_id": server_id,
        "stability_score": 100.0,
        "is_stable": True,
        "change_count": 0,
        "tier_transitions": [],
        "recommendations": [],
        "timestamp": utc_now_iso(),
    }

    # This would query historical data if available
    # For now, return baseline stable score
    sql = """
        SELECT
            server_id,
            risk_tier,
            trust_score,
            last_assessed
        FROM mcp_server_registry
        WHERE server_id = ?
    """
    current = ws_query(sql, [server_id])

    if not current:
        result["stability_score"] = 0.0
        result["is_stable"] = False
        result["recommendations"].append("No historical data available")
        return result

    # If we have historical tables, we'd query them here
    # Placeholder for historical stability computation
    result["stability_score"] = 85.0  # Baseline
    result["recommendations"].append("Maintain current monitoring cadence")

    return result


def analyze_server_axis_position(server_id: str) -> Dict[str, Any]:
    """
    Comprehensive analysis of a server's position on risk axis.
    Returns full deviation profile.
    """
    result: Dict[str, Any] = {
        "server_id": server_id,
        "axis_analysis": {},
        "signal_deviation": {},
        "stability": {},
        "overall_deviation_score": 0.0,
        "is_outlier": False,
        "timestamp": utc_now_iso(),
    }

    # Get server details
    sql = """
        SELECT
            server_id,
            name,
            risk_tier,
            trust_score,
            verdict,
            registry_source,
            last_assessed
        FROM mcp_server_registry
        WHERE server_id = ?
    """
    server = ws_query(sql, [server_id])

    if not server:
        result["error"] = "Server not found"
        return result

    server_data = server[0]
    result["server_name"] = server_data.get("name", "Unknown")

    # Analyze signal-level deviations
    signal_dims = ["trust_score", "security_posture", "community_trust", "supply_chain"]
    for dim in signal_dims:
        signal_dev = analyze_signal_deviation(server_id, dim)
        result["signal_deviation"][dim] = signal_dev

    # Analyze axis stability
    stability = compute_axis_stability_score(server_id)
    result["stability"] = stability

    # Compute overall deviation score
    scores = [s.get("deviation_score", 0) for s in result["signal_deviation"].values()]
    if scores:
        result["overall_deviation_score"] = round(sum(scores) / len(scores), 2)

    # Determine if outlier
    result["is_outlier"] = result["overall_deviation_score"] >= 60.0

    return result


def compute_registry_axis_metrics() -> Dict[str, Any]:
    """
    Compute overall registry axis metrics.
    Returns summary statistics for the entire registry.
    """
    result: Dict[str, Any] = {
        "total_servers": 0,
        "tier_distribution": {},
        "deviation_summary": {},
        "outlier_count": 0,
        "stable_count": 0,
        "timestamp": utc_now_iso(),
    }

    # Get server count
    count_sql = "SELECT COUNT(*) as total FROM mcp_server_registry"
    count_result = ws_query(count_sql)
    if count_result:
        result["total_servers"] = count_result[0].get("total", 0)

    # Get tier distribution
    tier_sql = """
        SELECT risk_tier, COUNT(*) as count
        FROM mcp_server_registry
        WHERE risk_tier IS NOT NULL
        GROUP BY risk_tier
    """
    tiers = ws_query(tier_sql)
    result["tier_distribution"] = {t["risk_tier"]: t["count"] for t in tiers}

    # Get outlier count
    outliers = identify_axis_outliers(limit=100, threshold=70)
    result["outlier_count"] = len(outliers)
    result["deviation_summary"]["high_risk_outliers"] = result["outlier_count"]

    # Stable servers (low deviation)
    stable_sql = """
        SELECT COUNT(*) as count
        FROM mcp_server_registry
        WHERE risk_tier IN ('LOW', 'MINIMAL', 'MEDIUM')
    """
    stable_result = ws_query(stable_sql)
    if stable_result:
        result["stable_count"] = stable_result[0].get("count", 0)

    return result


def get_axis_deviation_dashboard() -> Dict[str, Any]:
    """
    Generate dashboard data for axis deviation analysis.
    Returns comprehensive view for UI display.
    """
    dashboard: Dict[str, Any] = {
        "registry_metrics": compute_registry_axis_metrics(),
        "tier_analysis": analyze_risk_tier_axis(),
        "top_outliers": identify_axis_outliers(limit=10, threshold=50),
        "alerts": generate_deviation_alerts(threshold=70, limit=5),
        "generated_at": utc_now_iso(),
    }

    return dashboard


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------


def run() -> None:
    """Run the axis deviation analysis logic."""
    log.info("Starting server axis deviation analysis logic")

    try:
        # Compute registry metrics
        metrics = compute_registry_axis_metrics()
        log.info(
            "Registry metrics: total=%d, outliers=%d, stable=%d",
            metrics["total_servers"],
            metrics.get("outlier_count", 0),
            metrics["stable_count"],
        )

        # Analyze tier distribution
        tier_analysis = analyze_risk_tier_axis()
        log.info(
            "Tier analysis: %s",
            tier_analysis.get("deviation_analysis", {}).get("total_deviation", 0),
        )

        # Get top outliers
        outliers = identify_axis_outliers(limit=10, threshold=50)
        log.info("Found %d high-deviation servers", len(outliers))

        # Generate dashboard
        dashboard = get_axis_deviation_dashboard()
        log.info(
            "Dashboard generated: %d alerts, %d top outliers",
            len(dashboard["alerts"]),
            len(dashboard["top_outliers"]),
        )

        log.info("Axis deviation analysis completed successfully")

    except Exception as e:
        log.error("Axis deviation analysis failed: %s", e, exc_info=True)
        raise


if __name__ == "__main__":
    run()
    sys.exit(0)