import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

SERVICE_NAME = "server_threat_intel_summary_api"
PORT = 8791
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
WRITE_URL = f"{WRITE_SERVICE_URL}/write"
EXECUTE_URL = f"{WRITE_SERVICE_URL}/execute"
LOG_DIR = "/home/workspace/logs"
LOG_FILE = f"{LOG_DIR}/{SERVICE_NAME}.log"
HEALTH_CHECK_TIMEOUT = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(Sname__)

app = FastAPI(title="Server Threat Intel Summary API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_health_start_time = datetime.now(timezone.utc)


def ws_query(sql: str) -> List[Dict[str, Any]]:
    try:
        resp = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=HEALTH_CHECK_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except requests.exceptions.RequestException as e:
        log.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        resp = requests.post(
            WRITE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=HEALTH_CHECK_TIMEOUT,
        )
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Write failed for {table}: {e}")
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@app.get("/health")
def health():
    uptime = (datetime.now(timezone.utc) - _health_start_time).total_seconds()
    return JSONResponse({"status": "ok", "service": SERVICE_NAME, "uptime_seconds": uptime})


@app.get("/api/v1/servers/{server_id}/threat-summary")
def get_server_threat_summary(server_id: str):
    threat_associations = ws_query(
        f"""
        SELECT 
            threat_type,
            severity,
            evidence,
            reported_at
        FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
        ORDER BY reported_at DESC
        LIMIT 50
        """
    )

    signal_scores = ws_query(
        f"""
        SELECT 
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        """
    )

    risk_register = ws_query(
        f"""
        SELECT 
            risk_tier,
            risk_rank,
            threat_count,
            computed_at
        FROM mcp_risk_register
        WHERE server_id = '{server_id}'
        """
    )

    registry_info = ws_query(
        f"""
        SELECT 
            name,
            url,
            description,
            trust_score,
            verdict,
            registry_source,
            scan_count
        FROM mcp_server_registry
        WHERE server_id = '{server_id}'
        """
    )

    if not registry_info:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    return JSONResponse({
        "server_id": server_id,
        "registry": registry_info[0] if registry_info else None,
        "threat_associations": threat_associations,
        "signal_scores": signal_scores,
        "risk_register": risk_register[0] if risk_register else None,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/servers/{server_id}/threat-timeline")
def get_server_threat_timeline(
    server_id: str,
    days: int = Query(default=30, ge=1, le=365)
):
    cutoff = datetime.now(timezone.utc)
    from datetime import timedelta
    cutoff = cutoff - timedelta(days=days)
    cutoff_iso = cutoff.isoformat()

    threat_timeline = ws_query(
        f"""
        SELECT 
            threat_type,
            severity,
            reported_at
        FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
          AND reported_at >= '{cutoff_iso}'
        ORDER BY reported_at ASC
        """
    )

    signal_timeline = ws_query(
        f"""
        SELECT 
            signal_name,
            score,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
          AND scored_at >= '{cutoff_iso}'
        ORDER BY scored_at ASC
        """
    )

    return JSONResponse({
        "server_id": server_id,
        "period_days": days,
        "threat_timeline": threat_timeline,
        "signal_timeline": signal_timeline,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/threats/high-risk")
def get_high_risk_servers(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0)
):
    high_risk = ws_query(
        f"""
        SELECT 
            r.server_id,
            r.name,
            r.url,
            r.trust_score,
            r.verdict,
            rr.risk_tier,
            rr.risk_rank,
            rr.threat_count,
            rr.computed_at
        FROM mcp_risk_register rr
        JOIN mcp_server_registry r ON rr.server_id = r.server_id
        WHERE rr.risk_tier IN ('CRITICAL', 'HIGH', 'HIGH_RISK_ISOLATED')
        ORDER BY rr.risk_rank ASC, rr.threat_count DESC
        LIMIT {limit}
        OFFSET {offset}
        """
    )

    total_count = ws_query(
        """
        SELECT COUNT(*) as total
        FROM mcp_risk_register
        WHERE risk_tier IN ('CRITICAL', 'HIGH', 'HIGH_RISK_ISOLATED')
        """
    )

    return JSONResponse({
        "servers": high_risk,
        "total": total_count[0]["total"] if total_count else 0,
        "limit": limit,
        "offset": offset,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/threats/threat-types")
def get_threat_type_distribution():
    threat_types = ws_query(
        """
        SELECT 
            threat_type,
            severity,
            COUNT(*) as count,
            COUNT(DISTINCT server_id) as affected_servers
        FROM mcp_threat_associations
        GROUP BY threat_type, severity
        ORDER BY count DESC
        """
    )

    return JSONResponse({
        "threat_types": threat_types,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/threats/severity-distribution")
def get_severity_distribution():
    severity_dist = ws_query(
        """
        SELECT 
            severity,
            COUNT(*) as count,
            COUNT(DISTINCT server_id) as affected_servers
        FROM mcp_threat_associations
        GROUP BY severity
        ORDER BY 
            CASE severity
                WHEN 'CRITICAL' THEN 1
                WHEN 'HIGH' THEN 2
                WHEN 'MEDIUM' THEN 3
                WHEN 'LOW' THEN 4
                ELSE 5
            END
        """
    )

    return JSONResponse({
        "severity_distribution": severity_dist,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/servers/{server_id}/threat-patterns")
def get_server_threat_patterns(server_id: str):
    patterns = ws_query(
        f"""
        SELECT 
            threat_type,
            severity,
            evidence,
            reported_at
        FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
        ORDER BY reported_at DESC
        LIMIT 100
        """
    )

    related_servers = ws_query(
        f"""
        SELECT 
            ta2.server_id,
            r.name,
            r.url,
            COUNT(*) as shared_threats
        FROM mcp_threat_associations ta1
        JOIN mcp_threat_associations ta2 
            ON ta1.threat_type = ta2.threat_type
            AND ta1.server_id != ta2.server_id
        JOIN mcp_server_registry r ON ta2.server_id = r.server_id
        WHERE ta1.server_id = '{server_id}'
        GROUP BY ta2.server_id, r.name, r.url
        ORDER BY shared_threats DESC
        LIMIT 20
        """
    )

    return JSONResponse({
        "server_id": server_id,
        "patterns": patterns,
        "related_servers": related_servers,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/threat-intel/summary")
def get_global_threat_intel_summary():
    total_servers = ws_query("SELECT COUNT(*) as count FROM mcp_server_registry")
    servers_with_threats = ws_query(
        "SELECT COUNT(DISTINCT server_id) as count FROM mcp_threat_associations"
    )
    threat_associations_count = ws_query(
        "SELECT COUNT(*) as count FROM mcp_threat_associations"
    )

    top_threats = ws_query(
        """
        SELECT 
            threat_type,
            severity,
            COUNT(*) as count,
            MAX(reported_at) as last_seen
        FROM mcp_threat_associations
        GROUP BY threat_type, severity
        ORDER BY count DESC
        LIMIT 10
        """
    )

    verdict_distribution = ws_query(
        """
        SELECT 
            verdict,
            COUNT(*) as count
        FROM mcp_server_registry
        WHERE verdict IS NOT NULL
        GROUP BY verdict
        ORDER BY count DESC
        """
    )

    risk_distribution = ws_query(
        """
        SELECT 
            risk_tier,
            COUNT(*) as count
        FROM mcp_risk_register
        GROUP BY risk_tier
        ORDER BY count DESC
        """
    )

    return JSONResponse({
        "total_servers": total_servers[0]["count"] if total_servers else 0,
        "servers_with_threats": servers_with_threats[0]["count"] if servers_with_threats else 0,
        "total_threat_associations": threat_associations_count[0]["count"] if threat_associations_count else 0,
        "top_threats": top_threats,
        "verdict_distribution": verdict_distribution,
        "risk_distribution": risk_distribution,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/servers/{server_id}/risk-analysis")
def get_server_risk_analysis(server_id: str):
    registry = ws_query(
        f"""
        SELECT 
            server_id,
            name,
            url,
            trust_score,
            verdict,
            registry_source,
            scan_count,
            first_seen,
            last_seen
        FROM mcp_server_registry
        WHERE server_id = '{server_id}'
        """
    )

    if not registry:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    signals = ws_query(
        f"""
        SELECT 
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE server_id = '{server_id}'
        """
    )

    risk_record = ws_query(
        f"""
        SELECT 
            risk_tier,
            risk_rank,
            threat_count,
            computed_at
        FROM mcp_risk_register
        WHERE server_id = '{server_id}'
        """
    )

    threat_count = ws_query(
        f"""
        SELECT COUNT(*) as count FROM mcp_threat_associations
        WHERE server_id = '{server_id}'
        """
    )

    signal_names = [s["signal_name"] for s in signals]
    signal_avg = sum(s["score"] for s in signals) / len(signals) if signals else 0

    risk_factors = []
    if threat_count and threat_count[0]["count"] > 0:
        risk_factors.append({
            "factor": "Known Threat Associations",
            "severity": "HIGH",
            "detail": f"{threat_count[0]['count']} threat associations found"
        })

    if signal_avg < 0.3:
        risk_factors.append({
            "factor": "Low Signal Scores",
            "severity": "MEDIUM",
            "detail": f"Average signal score: {signal_avg:.2f}"
        })

    if registry[0]["verdict"] in ["UNTRUSTED", "KNOWN_THREAT"]:
        risk_factors.append({
            "factor": "Negative Verdict",
            "severity": "HIGH",
            "detail": f"Verdict: {registry[0]['verdict']}"
        })

    return JSONResponse({
        "server_id": server_id,
        "registry": registry[0],
        "signals": signals,
        "risk_record": risk_record[0] if risk_record else None,
        "threat_count": threat_count[0]["count"] if threat_count else 0,
        "signal_average": signal_avg,
        "risk_factors": risk_factors,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/threats/recent")
def get_recent_threats(
    limit: int = Query(default=50, ge=1, le=200),
    severity: Optional[str] = None
):
    severity_filter = ""
    if severity:
        severity_filter = f"AND severity = '{severity}'"

    recent = ws_query(
        f"""
        SELECT 
            ta.threat_type,
            ta.severity,
            ta.evidence,
            ta.reported_at,
            r.name as server_name,
            r.url as server_url
        FROM mcp_threat_associations ta
        JOIN mcp_server_registry r ON ta.server_id = r.server_id
        WHERE 1=1 {severity_filter}
        ORDER BY ta.reported_at DESC
        LIMIT {limit}
        """
    )

    return JSONResponse({
        "threats": recent,
        "count": len(recent),
        "filter_severity": severity,
        "generated_at": utc_now_iso(),
    })


@app.get("/api/v1/servers/search/by-threat")
def search_servers_by_threat(
    threat_type: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200)
):
    servers = ws_query(
        f"""
        SELECT 
            ta.server_id,
            r.name,
            r.url,
            r.verdict,
            ta.threat_type,
            ta.severity,
            ta.reported_at
        FROM mcp_threat_associations ta
        JOIN mcp_server_registry r ON ta.server_id = r.server_id
        WHERE ta.threat_type = '{threat_type}'
        ORDER BY ta.reported_at DESC
        LIMIT {limit}
        """
    )

    return JSONResponse({
        "threat_type": threat_type,
        "servers": servers,
        "count": len(servers),
        "generated_at": utc_now_iso(),
    })


def send_heartbeat():
    ws_write("service_health", [{
        "service": SERVICE_NAME,
        "status": "running",
        "ts": utc_now_iso(),
        "meta": {"port": PORT}
    }])


@app.on_event("startup")
def startup():
    log.info(f"{SERVICE_NAME} starting on port {PORT}")
    send_heartbeat()


def run():
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()