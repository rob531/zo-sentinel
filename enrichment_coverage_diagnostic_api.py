#!/usr/bin/env python3
"""
Enrichment Coverage Diagnostic API
FastAPI service on port 8795 that reports enrichment pipeline health.
Queries write_service HTTP API at 127.0.0.1:8772/query - no direct DB.
"""

import json
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse
import requests

app = FastAPI(title="Enrichment Coverage Diagnostic API")

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
SCAN_TIMESTAMP = datetime.utcnow().isoformat() + "Z"


def query_write_service(sql: str, params: Optional[dict] = None) -> list[dict]:
    """Query the write_service HTTP API."""
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"sql": sql, "params": params or {}},
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        if result.get("error"):
            raise Exception(f"Query error: {result['error']}")
        return result.get("rows", [])
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Write service unavailable: {e}")


def get_all_mcps() -> list[dict]:
    """Get all MCPs from registry."""
    sql = "SELECT mcp_identifier, mcp_name FROM mcp_server_registry"
    return query_write_service(sql)


def get_enrichments() -> list[dict]:
    """Get all enrichments from signal_enrichments table."""
    sql = "SELECT signal_type, mcp_identifier FROM mcp_signal_enrichments"
    return query_write_service(sql)


def get_all_signal_types() -> list[str]:
    """Get distinct signal types from enrichments."""
    sql = "SELECT DISTINCT signal_type FROM mcp_signal_enrichments"
    rows = query_write_service(sql)
    return [r["signal_type"] for r in rows]


def get_last_scan_time() -> Optional[str]:
    """Get timestamp of most recent enrichment."""
    sql = "SELECT MAX(timestamp) as last_ts FROM mcp_signal_enrichments"
    rows = query_write_service(sql)
    if rows and rows[0].get("last_ts"):
        return rows[0]["last_ts"]
    return SCAN_TIMESTAMP


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    try:
        # Quick connectivity check
        query_write_service("SELECT 1")
        last_scan = get_last_scan_time()
        return JSONResponse({
            "status": "healthy",
            "last_scan": last_scan,
            "pipeline_stalled": False
        })
    except Exception:
        return JSONResponse({
            "status": "degraded",
            "last_scan": get_last_scan_time(),
            "pipeline_stalled": True
        })


@app.get("/coverage/summary")
async def coverage_summary():
    """Get enrichment coverage summary."""
    all_mcps = get_all_mcps()
    enrichments = get_enrichments()
    total_mcps = len(all_mcps)
    
    # Build set of enriched MCPs
    enriched_identifiers = {e["mcp_identifier"] for e in enrichments}
    enriched_count = len(enriched_identifiers)
    
    # Calculate coverage
    coverage_pct = (enriched_count / total_mcps * 100) if total_mcps > 0 else 0.0
    
    # Build missing by type
    missing_by_type = {}
    all_signal_types = get_all_signal_types()
    
    for signal_type in all_signal_types:
        mcps_with_type = {e["mcp_identifier"] for e in enrichments if e["signal_type"] == signal_type}
        missing = [mcp for mcp in all_mcps if mcp["mcp_identifier"] not in mcps_with_type]
        missing_by_type[signal_type] = len(missing)
    
    return {
        "total_mcps": total_mcps,
        "enriched_count": enriched_count,
        "coverage_pct": round(coverage_pct, 2),
        "missing_by_type": missing_by_type
    }


@app.get("/coverage/gaps/{signal_type}")
async def coverage_gaps(
    signal_type: str,
    limit: int = Query(default=20, ge=1, le=1000)
):
    """Get MCPs missing a specific signal type enrichment."""
    all_mcps = get_all_mcps()
    enrichments = get_enrichments()
    
    # Find MCPs that have this signal type
    mcps_with_type = {e["mcp_identifier"] for e in enrichments if e["signal_type"] == signal_type}
    
    # Find missing MCPs
    missing_mcps = []
    for mcp in all_mcps:
        if mcp["mcp_identifier"] not in mcps_with_type:
            missing_mcps.append({
                "mcp_name": mcp["mcp_name"],
                "mcp_identifier": mcp["mcp_identifier"],
                "missing_since_days": None  # Would need timestamp data to calculate
            })
    
    # Sort by identifier for consistency
    missing_mcps.sort(key=lambda x: x["mcp_identifier"])
    
    return missing_mcps[:limit]


@app.get("/coverage/priority")
async def coverage_priority(
    limit: int = Query(default=20, ge=1, le=100)
):
    """Get top MCPs ranked by missing enrichment count."""
    all_mcps = get_all_mcps()
    enrichments = get_enrichments()
    all_signal_types = get_all_signal_types()
    
    # Build enrichment map: mcp_identifier -> set of signal_types
    enrichment_map: dict[str, set[str]] = {}
    for mcp in all_mcps:
        enrichment_map[mcp["mcp_identifier"]] = set()
    for e in enrichments:
        if e["mcp_identifier"] in enrichment_map:
            enrichment_map[e["mcp_identifier"]].add(e["signal_type"])
    
    # Calculate missing counts
    priority_list = []
    for mcp in all_mcps:
        identifier = mcp["mcp_identifier"]
        have_types = enrichment_map.get(identifier, set())
        missing_types = [st for st in all_signal_types if st not in have_types]
        
        if missing_types:  # Only include MCPs with missing enrichments
            priority_list.append({
                "mcp_name": mcp["mcp_name"],
                "mcp_identifier": identifier,
                "missing_enrichment_count": len(missing_types),
                "missing_types": missing_types
            })
    
    # Sort by missing count descending, then by name
    priority_list.sort(key=lambda x: (-x["missing_enrichment_count"], x["mcp_name"]))
    
    return priority_list[:limit]


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8795)