from fastapi import FastAPI, HTTPException, Header, Query
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import hashlib
import hmac
import os
import json
import sqlite3
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Axis Scores API", version="1.0.0")

WRITE_SERVICE_URL = "http://localhost:8772"
MESH_MEMORY_DB = "/home/workspace/Datasets/zo-mesh/mesh_memory.db"
API_SECRET = os.environ.get("AXIS_SCORES_API_SECRET", "")


def _verify_api_key(x_api_key: str = Header(None)) -> str:
    if not API_SECRET:
        return "unauthenticated"
    if not x_api_key or not hmac.compare_digest(x_api_key, API_SECRET):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


def _ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    import requests
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={"table": table, "rows": rows},
            timeout=10,
        )
        return resp.status_code in (200, 201)
    except Exception as exc:
        logger.error("ws_write failed for %s: %s", table, exc)
        return False


def _ws_query(sql: str) -> List[Dict[str, Any]]:
    import requests
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": sql},
            timeout=15,
        )
        data = resp.json()
        return data.get("rows", [])
    except Exception as exc:
        logger.error("ws_query failed: %s", exc)
        return []


def _mesh_memory_query(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(MESH_MEMORY_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def _mesh_memory_write(sql: str, params: tuple = ()) -> bool:
    conn = sqlite3.connect(MESH_MEMORY_DB)
    cur = conn.cursor()
    cur.execute(sql, params)
    conn.commit()
    rows_affected = cur.rowcount
    conn.close()
    return rows_affected > 0


@app.get("/health")
def health():
    return {"status": "ok", "service": "axis_scores_api", "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/registry/count")
def registry_count(_api_key: str = Header(None)):
    _verify_api_key(_api_key)
    rows = _ws_query("SELECT COUNT(*) AS cnt FROM mcp_server_registry")
    return {"count": rows[0]["cnt"] if rows else 0}


@app.get("/api/registry/servers")
def list_servers(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _api_key: str = Header(None),
):
    _verify_api_key(_api_key)
    sql = f"SELECT server_id, name, url, verdict, trust_score FROM mcp_server_registry ORDER BY server_id LIMIT {limit} OFFSET {offset}"
    rows = _ws_query(sql)
    return {"servers": rows, "limit": limit, "offset": offset}


@app.get("/api/registry/server/{server_id}")
def get_server(server_id: str, _api_key: str = Header(None)):
    _verify_api_key(_api_key)
    sql = "SELECT server_id, name, url, description, verdict, trust_score, registry_source, scan_count FROM mcp_server_registry WHERE server_id = ?"
    rows = _ws_query(sql)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return rows[0]


@app.get("/api/scores/{server_id}")
def get_signal_scores(server_id: str, _api_key: str = Header(None)):
    _verify_api_key(_api_key)
    sql = "SELECT server_id, signal_name, score, evidence, scored_at FROM mcp_signal_scores WHERE server_id = ?"
    rows = _ws_query(sql)
    return {"server_id": server_id, "signals": rows}


@app.post("/api/scores")
def post_signal_scores(
    payload: Dict[str, Any],
    _api_key: str = Header(None),
):
    _verify_api_key(_api_key)
    signals = payload.get("signals", [])
    if not signals:
        raise HTTPException(status_code=400, detail="signals list required")
    ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for sig in signals:
        rows.append({
            "server_id": str(sig["server_id"]),
            "signal_name": str(sig["signal_name"]),
            "score": float(sig["score"]),
            "evidence": json.dumps(sig.get("evidence", {})),
            "scored_at": ts,
        })
    ok = _ws_write("mcp_signal_scores", rows)
    if not ok:
        raise HTTPException(status_code=500, detail="Write to mcp_signal_scores failed")
    return {"ok": True, "count": len(rows)}


@app.get("/api/risk/{server_id}")
def get_risk_tier(server_id: str, _api_key: str = Header(None)):
    _verify_api_key(_api_key)
    sql = "SELECT server_id, risk_tier, risk_rank, threat_count, computed_at FROM mcp_risk_register WHERE server_id = ?"
    rows = _ws_query(sql)
    if not rows:
        raise HTTPException(status_code=404, detail=f"Risk record for {server_id} not found")
    return rows[0]


@app.get("/api/risk/summary")
def get_risk_summary(_api_key: str = Header(None)):
    _verify_api_key(_api_key)
    sql = "SELECT risk_tier, COUNT(*) AS cnt FROM mcp_risk_register GROUP BY risk_tier ORDER BY risk_tier"
    rows = _ws_query(sql)
    return {"distribution": rows}


@app.get("/api/mesh/memory/{server_id}")
def get_mesh_memory(server_id: str, _api_key: str = Header(None)):
    _verify_api_key(_api_key)
    sql = "SELECT server_id, key, value FROM mesh_memory WHERE server_id = ?"
    rows = _mesh_memory_query(sql, (server_id,))
    memory = {r["key"]: r["value"] for r in rows}
    return {"server_id": server_id, "memory": memory}


@app.post("/api/mesh/memory")
def set_mesh_memory(
    payload: Dict[str, Any],
    _api_key: str = Header(None),
):
    _verify_api_key(_api_key)
    server_id = str(payload.get("server_id", ""))
    memory = payload.get("memory", {})
    if not server_id:
        raise HTTPException(status_code=400, detail="server_id required")
    for k, v in memory.items():
        _mesh_memory_write(
            "INSERT OR REPLACE INTO mesh_memory (server_id, key, value) VALUES (?, ?, ?)",
            (server_id, str(k), str(v)),
        )
    return {"ok": True, "server_id": server_id}


@app.get("/api/threats/{server_id}")
def get_threats(server_id: str, _api_key: str = Header(None)):
    _verify_api_key(_api_key)
    sql = "SELECT server_id, threat_type, severity, evidence, reported_at FROM mcp_threat_associations WHERE server_id = ?"
    rows = _ws_query(sql)
    return {"server_id": server_id, "threats": rows}


@app.get("/api/verdicts")
def list_verdicts(
    verdict: Optional[str] = None,
    limit: int = Query(100, ge=1, le=1000),
    _api_key: str = Header(None),
):
    _verify_api_key(_api_key)
    if verdict:
        sql = f"SELECT server_id, name, verdict, trust_score FROM mcp_server_registry WHERE verdict = ? ORDER BY trust_score DESC LIMIT {limit}"
        rows = _ws_query(sql)
    else:
        sql = f"SELECT server_id, name, verdict, trust_score FROM mcp_server_registry ORDER BY trust_score DESC LIMIT {limit}"
        rows = _ws_query(sql)
    return {"verdicts": rows}


@app.get("/api/audit/{server_id}")
def get_audit_trail(server_id: str, _api_key: str = Header(None)):
    _verify_api_key(_api_key)
    sql = "SELECT event_id, event_type, actor, action, details_json, outcome, timestamp FROM audit_log WHERE target_server_id = ? ORDER BY timestamp DESC LIMIT 50"
    rows = _ws_query(sql)
    return {"server_id": server_id, "events": rows}


def run():
    import uvicorn
    port = int(os.environ.get("AXIS_SCORES_PORT", "8783"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    run()