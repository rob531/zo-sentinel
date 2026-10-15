import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772"
EXECUTE_SERVICE_URL = "http://localhost:8772"

SERVICE_NAME = "vuln_linker_v2"
PORT = 0


def ws_query(sql: str) -> Dict[str, Any]:
    """Query DuckDB via write_service HTTP endpoint."""
    try:
        resp = requests.post(
            f"{QUERY_SERVICE_URL}/query",
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"ws_query failed: {e}")
        return {"rows": [], "error": str(e)}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write rows to DuckDB via write_service HTTP endpoint."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows, "wait": True},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"ws_write failed: {e}")
        return {"error": str(e)}


def ws_execute(sql: str) -> Dict[str, Any]:
    """Execute DDL/DML via write_service HTTP endpoint."""
    try:
        resp = requests.post(
            f"{EXECUTE_SERVICE_URL}/execute",
            json={"sql": sql},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.error(f"ws_execute failed: {e}")
        return {"error": str(e)}


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def compute_deterministic_id(content: str) -> str:
    """Compute SHA256-based deterministic ID for idempotent writes."""
    import hashlib
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def link_vulnerability(
    server_id: str,
    threat_type: str,
    severity: str,
    evidence: str,
) -> Dict[str, Any]:
    """
    Link a vulnerability or threat to an MCP server.
    Creates an entry in mcp_threat_associations if one does not already exist.
    """
    now = utc_now_iso()
    threat_id = compute_deterministic_id(f"{server_id}:{threat_type}:{evidence}")
    
    existing = ws_query(
        f"SELECT * FROM mcp_threat_associations WHERE server_id = '{server_id}' AND threat_type = '{threat_type}' LIMIT 1"
    )
    
    if existing.get("rows") and len(existing["rows"]) > 0:
        return {
            "status": "exists",
            "threat_id": threat_id,
            "server_id": server_id,
            "threat_type": threat_type,
        }
    
    row = {
        "server_id": server_id,
        "threat_type": threat_type,
        "severity": severity,
        "evidence": evidence,
        "reported_at": now,
    }
    
    result = ws_write("mcp_threat_associations", [row])
    return {
        "status": "linked",
        "threat_id": threat_id,
        "server_id": server_id,
        "threat_type": threat_type,
        "severity": severity,
        "result": result,
    }


def get_server_threats(server_id: str) -> Dict[str, Any]:
    """Retrieve all threat associations for a given server."""
    result = ws_query(
        f"SELECT threat_type, severity, evidence, reported_at "
        f"FROM mcp_threat_associations "
        f"WHERE server_id = '{server_id}' "
        f"ORDER BY reported_at DESC"
    )
    return {
        "server_id": server_id,
        "threats": result.get("rows", []),
        "count": len(result.get("rows", [])),
    }


def get_servers_by_threat_type(threat_type: str) -> Dict[str, Any]:
    """Find all servers associated with a specific threat type."""
    result = ws_query(
        f"SELECT server_id, severity, evidence, reported_at "
        f"FROM mcp_threat_associations "
        f"WHERE threat_type = '{threat_type}' "
        f"ORDER BY reported_at DESC"
    )
    return {
        "threat_type": threat_type,
        "servers": result.get("rows", []),
        "count": len(result.get("rows", [])),
    }


def get_severity_distribution() -> Dict[str, Any]:
    """Get distribution of threats by severity level."""
    result = ws_query(
        "SELECT severity, COUNT(*) as count "
        "FROM mcp_threat_associations "
        "GROUP BY severity "
        "ORDER BY count DESC"
    )
    return {
        "distribution": result.get("rows", []),
        "total": sum(r.get("count", 0) for r in result.get("rows", [])),
    }


def get_high_risk_servers(min_severity: str = "HIGH") -> Dict[str, Any]:
    """Get servers with threats at or above specified severity level."""
    severity_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
    min_level = severity_order.get(min_severity.upper(), 3)
    
    threats_result = ws_query(
        "SELECT server_id, severity, threat_type, reported_at "
        "FROM mcp_threat_associations "
        "ORDER BY reported_at DESC"
    )
    
    high_risk = []
    for row in threats_result.get("rows", []):
        row_level = severity_order.get(row.get("severity", "").upper(), 0)
        if row_level >= min_level:
            high_risk.append(row)
    
    return {
        "min_severity": min_severity,
        "servers": high_risk,
        "count": len(high_risk),
    }


def get_threat_summary() -> Dict[str, Any]:
    """Get a summary of all threat associations across the registry."""
    total_threats = ws_query("SELECT COUNT(*) as total FROM mcp_threat_associations")
    by_type = ws_query(
        "SELECT threat_type, COUNT(*) as count FROM mcp_threat_associations GROUP BY threat_type"
    )
    by_severity = ws_query(
        "SELECT severity, COUNT(*) as count FROM mcp_threat_associations GROUP BY severity"
    )
    unique_servers = ws_query(
        "SELECT COUNT(DISTINCT server_id) as count FROM mcp_threat_associations"
    )
    
    return {
        "total_threats": total_threats.get("rows", [{"total": 0}])[0].get("total", 0),
        "affected_servers": unique_servers.get("rows", [{"count": 0}])[0].get("count", 0),
        "by_threat_type": by_type.get("rows", []),
        "by_severity": by_severity.get("rows", []),
        "generated_at": utc_now_iso(),
    }


def health() -> Dict[str, Any]:
    """Return health status of the vuln_linker service."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "timestamp": utc_now_iso(),
    }


if __name__ == "__main__":
    logger.info(f"{SERVICE_NAME} standalone health check")
    result = health()
    print(json.dumps(result, indent=2))
    sys.exit(0)