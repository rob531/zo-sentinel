#!/usr/bin/env python3
"""
comparison_api.py -- ZO-SENTINEL Phase 6
FastAPI comparison API on port 8785.
Compares two MCPs side-by-side across all dimensions.
"""
import json, logging, requests, time, uvicorn, threading
from datetime import datetime, timezone
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, Dict, Any, List

log = logging.getLogger(__name__)
WRITE_SERVICE = "http://127.0.0.1:8772"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
SERVICE_NAME = "comparison_api"
PORT = 8785
HEARTBEAT_INTERVAL = 30

app = FastAPI(title="ZO-SENTINEL Comparison API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

SIGNAL_WEIGHTS = {
    "attestation": 0.25,
    "authority_score": 0.15,
    "diversity_score": 0.10,
    "popularity_score": 0.10,
    "registration_longevity": 0.10,
    "security_audit": 0.15,
    "source_verification": 0.15,
}

RISK_TIER_QUERY = """
CASE 
    WHEN trust_score >= 0.8 THEN 'LOW'
    WHEN trust_score >= 0.6 THEN 'MEDIUM'
    WHEN trust_score >= 0.4 THEN 'HIGH'
    WHEN trust_score >= 0.2 THEN 'CRITICAL'
    ELSE 'UNKNOWN'
END
"""

def ws_query(sql: str) -> list:
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=15)
        if r.status_code == 200:
            return r.json().get("rows", [])
        log.warning(f"ws_query non-200: {r.status_code} for sql={sql[:100]}")
    except Exception as e:
        log.error(f"ws_query error: {e}")
    return []

def ws_write(table: str, rows: Dict[str, Any]) -> bool:
    try:
        payload = {"table": table, "rows": rows}
        r = requests.post(f"{WRITE_SERVICE}/write", json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_write error: {e}")
        return False

def send_heartbeat() -> None:
    try:
        ws_write("service_health", {
            "service": SERVICE_NAME,
            "last_heartbeat": datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")

def heartbeat_loop() -> None:
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

def start_heartbeat() -> None:
    t = threading.Thread(target=heartbeat_loop, daemon=True)
    t.start()

def resolve_server(identifier: str) -> Optional[Dict[str, Any]]:
    safe_id = identifier.replace("'", "''")
    rows = ws_query(
        f"SELECT server_id, name, url, description, trust_score, verdict, "
        f"verdict_reasoning, confidence, last_assessed, scan_count, "
        f"registry_source, {RISK_TIER_QUERY} as risk_tier "
        f"FROM mcp_server_registry "
        f"WHERE server_id = '{safe_id}' "
        f"OR name ILIKE '%{safe_id}%' "
        f"OR name = '{safe_id}' "
        f"ORDER BY last_assessed DESC NULLS LAST LIMIT 1"
    )
    return rows[0] if rows else None

def get_signal_scores(server_id: str) -> Dict[str, Any]:
    safe_id = server_id.replace("'", "''")
    rows = ws_query(
        f"SELECT signal_name, score, evidence, scored_at "
        f"FROM mcp_signal_scores "
        f"WHERE server_id = '{safe_id}'"
    )
    return {row["signal_name"]: row for row in rows}

def get_threat_count(server_id: str) -> int:
    safe_id = server_id.replace("'", "''")
    rows = ws_query(
        f"SELECT COUNT(*) as threat_count "
        f"FROM mcp_threat_associations "
        f"WHERE server_id = '{safe_id}'"
    )
    return rows[0]["threat_count"] if rows else 0

def get_attestation_status(server_id: str) -> Optional[Dict[str, Any]]:
    safe_id = server_id.replace("'", "''")
    rows = ws_query(
        f"SELECT id, server_id, attestation_level, attested_by, "
        f"attested_at, expires_at, status "
        f"FROM mcp_attestations "
        f"WHERE server_id = '{safe_id}' "
        f"ORDER BY attested_at DESC NULLS LAST LIMIT 1"
    )
    return rows[0] if rows else None

def compare_dimension(dim_name: str, a_score: Optional[float], b_score: Optional[float]) -> Dict[str, Any]:
    a_val = a_score if a_score is not None else 0.0
    b_val = b_score if b_score is not None else 0.0
    weight = SIGNAL_WEIGHTS.get(dim_name, 0.10)
    if a_val > b_val:
        winner = "a"
    elif b_val > a_val:
        winner = "b"
    else:
        winner = "equivalent"
    return {
        "dimension": dim_name,
        "weight": weight,
        "a_score": a_val,
        "b_score": b_val,
        "winner": winner
    }

def compute_risk_tier(trust_score: Optional[float]) -> str:
    if trust_score is None:
        return "UNKNOWN"
    if trust_score >= 0.8:
        return "LOW"
    elif trust_score >= 0.6:
        return "MEDIUM"
    elif trust_score >= 0.4:
        return "HIGH"
    elif trust_score >= 0.2:
        return "CRITICAL"
    return "UNKNOWN"

def determine_overall_recommendation(
    a_tier: str, b_tier: str, a_score_sum: float, b_score_sum: float,
    a_threats: int, b_threats: int, a_verdict: str, b_verdict: str
) -> str:
    verdict_order = {"TRUSTED": 0, "APPROVED": 1, "REVIEW": 2, "CAUTION": 3, "REJECT": 4, "UNKNOWN": 5}
    if a_verdict == "REJECT" and b_verdict == "REJECT":
        return "both_risky"
    if a_verdict == "REJECT":
        return "prefer_b"
    if b_verdict == "REJECT":
        return "prefer_a"
    if a_threats > 0 and b_threats == 0:
        return "prefer_b"
    if b_threats > 0 and a_threats == 0:
        return "prefer_a"
    if a_score_sum > b_score_sum + 0.5:
        return "prefer_a"
    elif b_score_sum > a_score_sum + 0.5:
        return "prefer_b"
    else:
        return "equivalent"

@app.get("/health")
def health():
    send_heartbeat()
    return {"status": "ok", "service": SERVICE_NAME, "port": PORT}

@app.get("/v1/compare")
def compare(
    a: str = Query(..., min_length=1, description="First server ID or name"),
    b: str = Query(..., min_length=1, description="Second server ID or name")
):
    a_info = resolve_server(a)
    b_info = resolve_server(b)
    if not a_info:
        return {"error": f"Server '{a}' not found in registry"}
    if not b_info:
        return {"error": f"Server '{b}' not found in registry"}
    a_sid = a_info["server_id"]
    b_sid = b_info["server_id"]
    a_signals = get_signal_scores(a_sid)
    b_signals = get_signal_scores(b_sid)
    a_threats = get_threat_count(a_sid)
    b_threats = get_threat_count(b_sid)
    a_attest = get_attestation_status(a_sid)
    b_attest = get_attestation_status(b_sid)
    signal_dims = []
    a_score_sum = 0.0
    b_score_sum = 0.0
    for dim_name, weight in SIGNAL_WEIGHTS.items():
        a_score = a_signals.get(dim_name, {}).get("score")
        b_score = b_signals.get(dim_name, {}).get("score")
        dim_cmp = compare_dimension(dim_name, a_score, b_score)
        signal_dims.append(dim_cmp)
        a_score_sum += (a_score if a_score is not None else 0.0) * weight
        b_score_sum += (b_score if b_score is not None else 0.0) * weight
    a_tier = compute_risk_tier(a_info.get("trust_score"))
    b_tier = compute_risk_tier(b_info.get("trust_score"))
    overall = determine_overall_recommendation(
        a_tier, b_tier, a_score_sum, b_score_sum,
        a_threats, b_threats,
        a_info.get("verdict", "UNKNOWN"),
        b_info.get("verdict", "UNKNOWN")
    )
    return {
        "overall_recommendation": overall,
        "server_a": {
            "identifier": a_info.get("name") or a_sid,
            "server_id": a_sid,
            "url": a_info.get("url"),
            "registry_source": a_info.get("registry_source"),
            "trust_score": a_info.get("trust_score"),
            "verdict": a_info.get("verdict"),
            "verdict_reasoning": a_info.get("verdict_reasoning"),
            "confidence": a_info.get("confidence"),
            "risk_tier": a_tier,
            "last_assessed": a_info.get("last_assessed"),
            "scan_count": a_info.get("scan_count"),
            "threat_count": a_threats,
            "attestation": a_attest
        },
        "server_b": {
            "identifier": b_info.get("name") or b_sid,
            "server_id": b_sid,
            "url": b_info.get("url"),
            "registry_source": b_info.get("registry_source"),
            "trust_score": b_info.get("trust_score"),
            "verdict": b_info.get("verdict"),
            "verdict_reasoning": b_info.get("verdict_reasoning"),
            "confidence": b_info.get("confidence"),
            "risk_tier": b_tier,
            "last_assessed": b_info.get("last_assessed"),
            "scan_count": b_info.get("scan_count"),
            "threat_count": b_threats,
            "attestation": b_attest
        },
        "signal_comparison": signal_dims,
        "weighted_score_a": round(a_score_sum, 3),
        "weighted_score_b": round(b_score_sum, 3)
    }

@app.get("/v1/rank")
def rank(
    tier: str = Query(..., description="Risk tier: LOW, MEDIUM, HIGH, CRITICAL"),
    limit: int = Query(10, ge=1, le=100, description="Max results")
):
    safe_tier = tier.upper().strip()
    valid_tiers = ["LOW", "MEDIUM", "HIGH", "CRITICAL", "UNKNOWN"]
    if safe_tier not in valid_tiers:
        return {"error": f"Invalid tier. Use one of: {valid_tiers}"}
    rows = ws_query(
        f"SELECT server_id, name, url, trust_score, verdict, "
        f"confidence, last_assessed, scan_count, "
        f"{RISK_TIER_QUERY} as risk_tier "
        f"FROM mcp_server_registry "
        f"WHERE {RISK_TIER_QUERY} = '{safe_tier}' "
        f"ORDER BY trust_score DESC NULLS LAST, "
        f"confidence DESC NULLS LAST "
        f"LIMIT {limit}"
    )
    ranked = []
    for pos, row in enumerate(rows, 1):
        threats = get_threat_count(row["server_id"])
        attest = get_attestation_status(row["server_id"])
        ranked.append({
            "rank": pos,
            "server_id": row["server_id"],
            "name": row["name"],
            "url": row["url"],
            "trust_score": row["trust_score"],
            "verdict": row["verdict"],
            "confidence": row["confidence"],
            "last_assessed": row["last_assessed"],
            "scan_count": row["scan_count"],
            "threat_count": threats,
            "attested": attest is not None,
            "attestation_level": attest.get("attestation_level") if attest else None
        })
    return {
        "tier": safe_tier,
        "count": len(ranked),
        "servers": ranked
    }

def run() -> None:
    start_heartbeat()
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")

if __name__ == "__main__":
    run()