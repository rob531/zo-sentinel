#!/usr/bin/env python3
"""
search_api.py -- ZO-SENTINEL Phase 6
FastAPI Search API on port 8782.
Enterprise search across MCP server registry with filtering and analytics.
"""
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

SERVICE_NAME = "search_api"
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
EXECUTE_URL = "http://127.0.0.1:8772"
PORT = 8782
HEARTBEAT_INTERVAL = 60

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="ZO-SENTINEL Search API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def ws_query(sql: str, params: Optional[list] = None) -> list:
    """Execute SQL query against DuckDB via write_service."""
    try:
        payload = {"sql": sql}
        if params:
            payload["params"] = params
        resp = requests.post(f"{EXECUTE_URL}/query", json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])
    except requests.RequestException as e:
        log.error(f"ws_query error: {e}")
        return []


def ws_write(table: str, rows: dict, wait: bool = True) -> dict:
    """Write to DuckDB via write_service."""
    url = f"{WRITE_SERVICE_URL}/write"
    payload = {"table": table, "rows": rows, "wait": wait}
    resp = requests.post(url, json=payload)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat():
    """Send service heartbeat to service_health table."""
    try:
        ws_write(
            "service_health",
            {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "healthy",
            },
        )
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


@app.get("/health")
def health():
    """Service health check."""
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "port": PORT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/search")
def search(
    q: str = Query("", description="Search query for name/description"),
    limit: int = Query(20, ge=1, le=100, description="Result limit"),
    verdict: Optional[str] = Query(None, description="Filter by verdict"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Search MCP servers by name and description.
    Supports optional filtering by verdict and risk_tier.
    """
    conditions = []
    params = []
    
    if q:
        # Multi-term AND search: each word must appear in name OR description
        terms = [t.strip() for t in q.split() if t.strip()]
        for term in terms:
            conditions.append("(name ILIKE ? OR description ILIKE ?)")
            pattern = f"%{term}%"
            params.extend([pattern, pattern])
    
    if verdict:
        conditions.append("verdict = ?")
        params.append(verdict)
    
    if risk_tier:
        conditions.append("risk_tier = ?")
        params.append(risk_tier)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT 
            server_id,
            name,
            url,
            description,
            verdict,
            trust_score,
            risk_tier,
            last_assessed,
            scan_count
        FROM mcp_server_registry
        WHERE {where_clause}
        ORDER BY 
            CASE WHEN trust_score IS NOT NULL THEN trust_score ELSE 0 END DESC,
            last_assessed DESC NULLS LAST
        LIMIT ?
        OFFSET ?
    """
    params.extend([limit, offset])
    
    results = ws_query(sql, params)
    
    total_sql = f"SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE {where_clause}"
    total_result = ws_query(total_sql, params[:-2])
    total = total_result[0]["cnt"] if total_result else 0
    
    return {
        "query": q,
        "filters": {"verdict": verdict, "risk_tier": risk_tier},
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": results,
    }


@app.get("/mcp/{server_id}")
def mcp_detail(server_id: str):
    """
    Full detail for an MCP server.
    Includes registry record, all signals, all threats, risk register, and latest attestation.
    """
    registry_rows = ws_query(
        """
        SELECT 
            server_id,
            name,
            registry_source,
            url,
            description,
            trust_score,
            verdict,
            verdict_reasoning,
            confidence,
            last_assessed,
            first_seen,
            last_seen,
            scan_count
        FROM mcp_server_registry
        WHERE server_id = ?
        LIMIT 1
        """,
        [server_id],
    )
    
    if not registry_rows:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    
    registry = registry_rows[0]
    
    signals = ws_query(
        """
        SELECT 
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = ?
        ORDER BY score DESC
        """,
        [server_id],
    )
    
    threats = ws_query(
        """
        SELECT 
            threat_type,
            evidence,
            severity,
            reported_at
        FROM mcp_threat_associations
        WHERE server_id = ?
        ORDER BY reported_at DESC
        """,
        [server_id],
    )
    
    definition_history = ws_query(
        """
        SELECT 
            snapshot_hash,
            captured_at
        FROM mcp_definition_history
        WHERE server_id = ?
        ORDER BY captured_at DESC
        LIMIT 10
        """,
        [server_id],
    )
    
    attestations = ws_query(
        """
        SELECT 
            attestation_id,
            attestation_hash,
            attestor,
            issued_at,
            expires_at,
            status
        FROM mcp_attestations
        WHERE server_id = ?
        ORDER BY issued_at DESC
        LIMIT 1
        """,
        [server_id],
    )
    
    risk_register = []
    if threats:
        severity_weights = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        for threat in threats:
            weight = severity_weights.get(threat.get("severity", "LOW"), 0)
            risk_register.append({
                "threat_type": threat.get("threat_type"),
                "severity": threat.get("severity"),
                "risk_level": weight * 3,
                "evidence": threat.get("evidence"),
                "reported_at": threat.get("reported_at"),
            })
    
    risk_register.sort(key=lambda x: x["risk_level"], reverse=True)
    
    return {
        "server_id": server_id,
        "registry": registry,
        "signals": signals,
        "threats": threats,
        "risk_register": risk_register,
        "attestation": attestations[0] if attestations else None,
        "definition_history": definition_history,
        "metadata": {
            "signals_count": len(signals),
            "threats_count": len(threats),
            "risk_items_count": len(risk_register),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    }


@app.get("/threats")
def threats(
    severity: Optional[str] = Query(None, description="Filter by severity"),
    limit: int = Query(20, ge=1, le=100, description="Result limit"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Recent threat intelligence feed.
    Shows all threat associations across MCP servers.
    """
    conditions = []
    params = []
    
    if severity:
        conditions.append("severity = ?")
        params.append(severity)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT 
            ta.id,
            ta.server_id,
            mr.name as server_name,
            ta.threat_type,
            ta.evidence,
            ta.severity,
            ta.reported_at
        FROM mcp_threat_associations ta
        LEFT JOIN mcp_server_registry mr ON ta.server_id = mr.server_id
        WHERE {where_clause}
        ORDER BY ta.reported_at DESC
        LIMIT ?
        OFFSET ?
    """
    params.extend([limit, offset])
    
    results = ws_query(sql, params)
    
    severity_counts = ws_query(
        f"""
        SELECT severity, COUNT(*) as cnt 
        FROM mcp_threat_associations 
        WHERE {where_clause if conditions else '1=1'}
        GROUP BY severity
        """,
        params[:-2] if conditions else None,
    )
    
    return {
        "filters": {"severity": severity},
        "limit": limit,
        "offset": offset,
        "severity_breakdown": {s["severity"]: s["cnt"] for s in severity_counts},
        "threats": results,
    }


@app.get("/risks")
def risks(
    tier: Optional[str] = Query(None, description="Filter by risk_tier"),
    limit: int = Query(20, ge=1, le=100, description="Result limit"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
):
    """
    Risk register showing servers by risk tier.
    Combines threat associations with trust scores for risk assessment.
    """
    conditions = []
    params = []
    
    if tier:
        conditions.append("risk_tier = ?")
        params.append(tier)
    
    where_clause = " AND ".join(conditions) if conditions else "1=1"
    
    sql = f"""
        SELECT 
            mr.server_id,
            mr.name,
            mr.url,
            mr.risk_tier,
            mr.trust_score,
            mr.verdict,
            mr.last_assessed,
            COUNT(ta.id) as threat_count,
            MAX(CASE WHEN ta.severity = 'CRITICAL' THEN 1 ELSE 0 END) as has_critical,
            MAX(CASE WHEN ta.severity = 'HIGH' THEN 1 ELSE 0 END) as has_high
        FROM mcp_server_registry mr
        LEFT JOIN mcp_threat_associations ta ON mr.server_id = ta.server_id
        WHERE {where_clause}
        GROUP BY mr.server_id, mr.name, mr.url, mr.risk_tier, 
                 mr.trust_score, mr.verdict, mr.last_assessed
        ORDER BY 
            has_critical DESC,
            has_high DESC,
            threat_count DESC,
            mr.trust_score ASC NULLS FIRST
        LIMIT ?
        OFFSET ?
    """
    params.extend([limit, offset])
    
    results = ws_query(sql, params)
    
    for r in results:
        risk_score = 0
        if r.get("has_critical"):
            risk_score += 20
        if r.get("has_high"):
            risk_score += 10
        risk_score += (r.get("threat_count", 0) or 0) * 2
        if r.get("trust_score") is None:
            risk_score += 15
        elif r.get("trust_score", 1) < 0.5:
            risk_score += 5
        r["risk_score"] = min(risk_score, 100)
    
    return {
        "filters": {"tier": tier},
        "limit": limit,
        "offset": offset,
        "risk_register": results,
    }


@app.get("/stats")
def stats():
    """
    Analytics dashboard statistics.
    Returns counts by verdict and risk_tier.
    """
    verdict_counts = ws_query(
        """
        SELECT verdict, COUNT(*) as cnt 
        FROM mcp_server_registry 
        GROUP BY verdict
        ORDER BY cnt DESC
        """
    )
    
    risk_tier_counts = ws_query(
        """
        SELECT risk_tier, COUNT(*) as cnt 
        FROM mcp_server_registry 
        GROUP BY risk_tier
        ORDER BY cnt DESC
        """
    )
    
    threat_severity_counts = ws_query(
        """
        SELECT severity, COUNT(*) as cnt 
        FROM mcp_threat_associations 
        GROUP BY severity
        """
    )
    
    total_servers = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    total_threats = ws_query("SELECT COUNT(*) as cnt FROM mcp_threat_associations")
    total_signals = ws_query("SELECT COUNT(*) as cnt FROM mcp_signal_scores")
    
    avg_trust_score = ws_query(
        """
        SELECT AVG(trust_score) as avg_score 
        FROM mcp_server_registry 
        WHERE trust_score IS NOT NULL
        """
    )
    
    verdict_30d = ws_query(
        """
        SELECT verdict, COUNT(*) as cnt 
        FROM mcp_server_registry 
        WHERE last_assessed >= NOW() - INTERVAL '30 days'
        GROUP BY verdict
        """
    )
    
    return {
        "totals": {
            "servers": total_servers[0]["cnt"] if total_servers else 0,
            "threats": total_threats[0]["cnt"] if total_threats else 0,
            "signals": total_signals[0]["cnt"] if total_signals else 0,
        },
        "by_verdict": {v["verdict"]: v["cnt"] for v in verdict_counts},
        "by_risk_tier": {rt["risk_tier"]: rt["cnt"] for rt in risk_tier_counts},
        "by_threat_severity": {s["severity"]: s["cnt"] for s in threat_severity_counts},
        "avg_trust_score": round(avg_trust_score[0]["avg_score"], 3) if avg_trust_score and avg_trust_score[0]["avg_score"] else None,
        "verdicts_30d": {v["verdict"]: v["cnt"] for v in verdict_30d},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run():
    """Main entry point for daemon operation."""
    send_heartbeat()
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()