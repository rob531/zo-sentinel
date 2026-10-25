"""
Overview Dashboard Logic

Provides aggregate statistics and metrics for the Sentinel dashboard overview.
All queries use ONLY real bus tables: mcp_server_registry, mcp_signal_scores,
mcp_threat_associations, mcp_risk_register, service_health.
"""

from typing import Any

import httpx

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _query(sql: str) -> list[dict[str, Any]]:
    """Execute a SELECT query against the write service."""
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            WRITE_SERVICE_URL,
            json={"sql": sql},
        )
        response.raise_for_status()
        data = response.json()
        return data.get("rows", [])


def get_registry_overview() -> dict[str, Any]:
    """
    Return high-level registry statistics.
    Uses mcp_server_registry and mcp_risk_register.
    """
    rows = _query("""
        SELECT
            COUNT(*) AS total_servers,
            COUNT(CASE WHEN verdict = 'TRUSTED' THEN 1 END) AS trusted_count,
            COUNT(CASE WHEN verdict = 'AMBER' THEN 1 END) AS amber_count,
            COUNT(CASE WHEN verdict = 'UNTRUSTED' THEN 1 END) AS untrusted_count,
            COUNT(CASE WHEN verdict = 'UNKNOWN' THEN 1 END) AS unknown_count,
            COUNT(CASE WHEN verdict = 'KNOWN_THREAT' THEN 1 END) AS known_threat_count,
            AVG(trust_score) AS avg_trust_score,
            MAX(trust_score) AS max_trust_score,
            MIN(trust_score) AS min_trust_score
        FROM mcp_server_registry
    """)

    if not rows:
        return {
            "total_servers": 0,
            "trusted_count": 0,
            "amber_count": 0,
            "untrusted_count": 0,
            "unknown_count": 0,
            "known_threat_count": 0,
            "avg_trust_score": 0.0,
            "max_trust_score": 0,
            "min_trust_score": 0,
        }

    row = rows[0]
    return {
        "total_servers": int(row.get("total_servers", 0) or 0),
        "trusted_count": int(row.get("trusted_count", 0) or 0),
        "amber_count": int(row.get("amber_count", 0) or 0),
        "untrusted_count": int(row.get("untrusted_count", 0) or 0),
        "unknown_count": int(row.get("unknown_count", 0) or 0),
        "known_threat_count": int(row.get("known_threat_count", 0) or 0),
        "avg_trust_score": float(row.get("avg_trust_score", 0) or 0),
        "max_trust_score": float(row.get("max_trust_score", 0) or 0),
        "min_trust_score": float(row.get("min_trust_score", 0) or 0),
    }


def get_risk_register_summary() -> dict[str, Any]:
    """
    Return risk register tier distribution.
    Uses mcp_risk_register.
    """
    rows = _query("""
        SELECT
            risk_tier,
            COUNT(*) AS server_count,
            AVG(threat_count) AS avg_threat_count
        FROM mcp_risk_register
        GROUP BY risk_tier
        ORDER BY risk_tier
    """)

    tiers = {}
    for row in rows:
        tier = row.get("risk_tier", "UNKNOWN")
        tiers[tier] = {
            "server_count": int(row.get("server_count", 0) or 0),
            "avg_threat_count": float(row.get("avg_threat_count", 0) or 0),
        }

    return tiers


def get_signal_score_summary() -> dict[str, Any]:
    """
    Return aggregate signal scores per signal type.
    Uses mcp_signal_scores.
    """
    rows = _query("""
        SELECT
            signal_name,
            COUNT(*) AS score_count,
            AVG(score) AS avg_score,
            MIN(score) AS min_score,
            MAX(score) AS max_score,
            STDDEV(score) AS stddev_score
        FROM mcp_signal_scores
        GROUP BY signal_name
        ORDER BY signal_name
    """)

    signals = {}
    for row in rows:
        name = row.get("signal_name", "unknown")
        signals[name] = {
            "score_count": int(row.get("score_count", 0) or 0),
            "avg_score": float(row.get("avg_score", 0) or 0),
            "min_score": float(row.get("min_score", 0) or 0),
            "max_score": float(row.get("max_score", 0) or 0),
            "stddev_score": float(row.get("stddev_score", 0) or 0),
        }

    return signals


def get_threat_summary() -> dict[str, Any]:
    """
    Return threat association statistics.
    Uses mcp_threat_associations.
    """
    rows = _query("""
        SELECT
            threat_type,
            severity,
            COUNT(*) AS association_count,
            MIN(reported_at) AS first_reported,
            MAX(reported_at) AS last_reported
        FROM mcp_threat_associations
        GROUP BY threat_type, severity
        ORDER BY severity DESC, association_count DESC
    """)

    threats = []
    total_count = 0
    for row in rows:
        count = int(row.get("association_count", 0) or 0)
        total_count += count
        threats.append({
            "threat_type": row.get("threat_type", "unknown"),
            "severity": row.get("severity", "UNKNOWN"),
            "association_count": count,
            "first_reported": row.get("first_reported"),
            "last_reported": row.get("last_reported"),
        })

    return {
        "total_threat_associations": total_count,
        "threats": threats,
    }


def get_service_health_summary() -> dict[str, Any]:
    """
    Return service health status for all daemons.
    Uses service_health.
    """
    rows = _query("""
        SELECT
            service,
            last_heartbeat
        FROM service_health
        ORDER BY service
    """)

    services = {}
    for row in rows:
        services[row.get("service", "unknown")] = {
            "last_heartbeat": row.get("last_heartbeat"),
        }

    return services


def get_dashboard_overview() -> dict[str, Any]:
    """
    Compose a full dashboard overview from all available statistics.
    Returns all metrics in a single structured response.
    """
    return {
        "registry": get_registry_overview(),
        "risk_tiers": get_risk_register_summary(),
        "signals": get_signal_score_summary(),
        "threats": get_threat_summary(),
        "services": get_service_health_summary(),
    }