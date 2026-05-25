#!/usr/bin/env python3
"""
registry_api.py -- ZO-SENTINEL Phase 6
FastAPI REST assessment API on port 8781.
All DB reads via write_service query endpoint on port 8772.

Endpoints:
  GET /v1/assess?mcp=<identifier>  -- full assessment for one MCP
  GET /v1/registry?page=&limit=    -- paginated assessed MCP list
  GET /v1/threats?limit=           -- recent threat intelligence
  GET /health                      -- service health
"""
import json, logging, requests, uvicorn
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

log = logging.getLogger(__name__)
WRITE_SERVICE = "http://127.0.0.1:8772"
PORT = 8781

app = FastAPI(title="ZO-SENTINEL Registry API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


def ws_query(sql: str) -> list:
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=10)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query: {e}")
    return []


@app.get("/health")
def health():
    return {"status": "ok", "service": "registry_api", "port": PORT}


@app.get("/v1/assess")
def assess(mcp: str = Query(..., min_length=1)):
    """Full assessment for an MCP identifier."""
    rows = ws_query(
        f"SELECT server_id, name, url, description, verdict, trust_score, "
        f"verdict_reasoning, confidence, last_assessed, scan_count "
        f"FROM mcp_server_registry "
        f"WHERE name ILIKE '%{mcp}%' OR url ILIKE '%{mcp}%' "
        f"OR server_id ILIKE '%{mcp}%' "
        f"ORDER BY last_assessed DESC NULLS LAST LIMIT 1"
    )
    if not rows:
        return {
            "mcp": mcp,
            "verdict": "INSUFFICIENT",
            "trust_score": None,
            "reasoning": "No assessment data found.",
            "signals": [],
            "threats": [],
            "attestation": None
        }
    rec = rows[0]
    server_id = rec.get("server_id", "")

    signals = ws_query(
        f"SELECT signal_name, score, evidence FROM mcp_signal_scores "
        f"WHERE server_id='{server_id}' ORDER BY scored_at DESC LIMIT 12"
    )
    seen, deduped = set(), []
    for s in signals:
        if s["signal_name"] not in seen:
            seen.add(s["signal_name"]); deduped.append(s)

    threats = ws_query(
        f"SELECT threat_type, severity, evidence, reported_at "
        f"FROM mcp_threat_associations WHERE server_id='{server_id}' "
        f"ORDER BY reported_at DESC LIMIT 5"
    )

    attestation = None
    attest_rows = ws_query(
        f"SELECT attestation_text, scope, confidence_level, valid_until, caveats "
        f"FROM mcp_attestations WHERE server_id='{server_id}' "
        f"ORDER BY generated_at DESC LIMIT 1"
    )
    if attest_rows:
        attestation = attest_rows[0]

    return {
        "mcp":        mcp,
        "server_id":  server_id,
        "name":       rec.get("name"),
        "url":        rec.get("url"),
        "verdict":    rec.get("verdict", "INSUFFICIENT"),
        "trust_score": rec.get("trust_score"),
        "reasoning":  rec.get("verdict_reasoning"),
        "confidence": rec.get("confidence"),
        "last_assessed": rec.get("last_assessed"),
        "signals":    deduped,
        "threats":    threats,
        "attestation": attestation
    }


@app.get("/v1/registry")
def registry(
    page:    int = Query(1, ge=1),
    limit:   int = Query(20, ge=1, le=100),
    verdict: Optional[str] = None
):
    """Paginated list of assessed MCPs."""
    offset = (page - 1) * limit
    where  = f"WHERE verdict='{verdict}'" if verdict else ""
    rows   = ws_query(
        f"SELECT server_id, name, url, verdict, trust_score, last_assessed "
        f"FROM mcp_server_registry {where} "
        f"ORDER BY last_assessed DESC NULLS LAST "
        f"LIMIT {limit} OFFSET {offset}"
    )
    return {"page": page, "limit": limit, "results": rows, "count": len(rows)}


@app.get("/v1/threats")
def threats(limit: int = Query(20, ge=1, le=100)):
    """Recent threat intelligence feed."""
    rows = ws_query(
        f"SELECT t.server_id, r.name, t.threat_type, t.severity, "
        f"t.evidence, t.reported_at "
        f"FROM mcp_threat_associations t "
        f"LEFT JOIN mcp_server_registry r ON t.server_id = r.server_id "
        f"ORDER BY t.reported_at DESC LIMIT {limit}"
    )
    return {"threats": rows, "count": len(rows)}


def run():
    logging.basicConfig(level=logging.INFO)
    log.info(f"ZO-SENTINEL Registry API starting on port {PORT}")
    uvicorn.run("registry_api:app", host="0.0.0.0", port=PORT,
                log_level="info", access_log=False)


if __name__ == "__main__":
    run()