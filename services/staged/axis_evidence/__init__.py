import logging
import sqlite3
import os
from fastapi import FastAPI, HTTPException
from typing import Optional, Dict, Any, List
from datetime import datetime
import uvicorn
import requests

SERVICE_NAME = "axis_evidence"
PORT = 8778
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
MESH_MEMORY_DB = "/home/workspace/Datasets/zo-mesh/mesh_memory.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log")]
)
log = logging.getLogger(__name__)

app = FastAPI()

_cache: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = 300


def _cache_get(server_id: int) -> Optional[Dict[str, Any]]:
    key = str(server_id)
    if key in _cache:
        entry, ts = _cache[key]
        if (datetime.utcnow() - ts).total_seconds() < _CACHE_TTL_SECONDS:
            return entry
    return None


def _cache_put(server_id: int, data: Dict[str, Any]) -> None:
    _cache[str(server_id)] = (data, datetime.utcnow())


def get_mesh_memory(server_id: int) -> Optional[str]:
    """Retrieve mesh memory for a given server_id from the SQLite mesh_memory store."""
    if not os.path.exists(MESH_MEMORY_DB):
        log.warning(f"mesh_memory.db not found at {MESH_MEMORY_DB}")
        return None
    try:
        conn = sqlite3.connect(MESH_MEMORY_DB)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT memory FROM mesh_memory WHERE server_id = ?",
            (server_id,)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return row[0]
        return None
    except sqlite3.Error as e:
        log.error(f"Error reading mesh_memory for server_id={server_id}: {e}")
        return None


def get_signal_scores(server_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve signal scores for a given server_id from DuckDB via write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": f"SELECT server_id, signal_name, score, evidence, scored_at FROM mcp_signal_scores WHERE server_id = '{server_id}'"},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rows"):
            return {"server_id": server_id, "signals": data["rows"]}
        return None
    except requests.RequestException as e:
        log.error(f"Error querying signal_scores for server_id={server_id}: {e}")
        return None


def get_server_metadata(server_id: int) -> Optional[Dict[str, Any]]:
    """Retrieve server registry metadata from DuckDB via write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": f"SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count FROM mcp_server_registry WHERE server_id = '{server_id}'"},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rows"):
            return data["rows"][0]
        return None
    except requests.RequestException as e:
        log.error(f"Error querying registry for server_id={server_id}: {e}")
        return None


def get_attestations(server_id: int) -> Optional[List[Dict[str, Any]]]:
    """Retrieve attestations for a given server_id from DuckDB via write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": f"SELECT server_id, attested_at, attestor, scope, evidence_hash FROM mcp_attestations WHERE server_id = '{server_id}'"},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rows"):
            return data["rows"]
        return None
    except requests.RequestException as e:
        log.error(f"Error querying attestations for server_id={server_id}: {e}")
        return None


def get_threat_associations(server_id: int) -> Optional[List[Dict[str, Any]]]:
    """Retrieve threat associations for a given server_id from DuckDB via write_service."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={"sql": f"SELECT server_id, threat_type, severity, evidence, reported_at FROM mcp_threat_associations WHERE server_id = '{server_id}'"},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("rows"):
            return data["rows"]
        return None
    except requests.RequestException as e:
        log.error(f"Error querying threat_associations for server_id={server_id}: {e}")
        return None


@app.get("/health")
def health():
    return {"status": "ok", "service": SERVICE_NAME}


@app.get("/evidence/{server_id}")
def get_evidence(server_id: int):
    """Return complete evidence package for a server."""
    cached = _cache_get(server_id)
    if cached:
        return cached

    metadata = get_server_metadata(server_id)
    if not metadata:
        raise HTTPException(status_code=404, detail=f"server_id={server_id} not found in registry")

    mesh_memory = get_mesh_memory(server_id)
    signal_scores = get_signal_scores(server_id)
    attestations = get_attestations(server_id)
    threat_associations = get_threat_associations(server_id)

    evidence = {
        "server_id": server_id,
        "metadata": metadata,
        "mesh_memory": mesh_memory,
        "signal_scores": signal_scores,
        "attestations": attestations,
        "threat_associations": threat_associations,
        "fetched_at": datetime.utcnow().isoformat() + "Z"
    }

    _cache_put(server_id, evidence)
    return evidence


@app.get("/mesh_memory/{server_id}")
def get_mesh_memory_endpoint(server_id: int):
    """Return only the mesh memory for a server."""
    memory = get_mesh_memory(server_id)
    if memory is None:
        raise HTTPException(status_code=404, detail=f"mesh_memory not found for server_id={server_id}")
    return {"server_id": server_id, "memory": memory}


@app.get("/signals/{server_id}")
def get_signals_endpoint(server_id: int):
    """Return only the signal scores for a server."""
    scores = get_signal_scores(server_id)
    if not scores:
        raise HTTPException(status_code=404, detail=f"signal_scores not found for server_id={server_id}")
    return scores


def send_heartbeat():
    """Write a heartbeat row to service_health via write_service."""
    try:
        requests.post(
            f"{WRITE_SERVICE_URL}/write",
            json={
                "table": "service_health",
                "rows": [{
                    "service": SERVICE_NAME,
                    "last_heartbeat": datetime.utcnow().isoformat() + "Z",
                    "status": "ok"
                }]
            },
            timeout=10
        )
    except requests.RequestException as e:
        log.warning(f"Heartbeat failed: {e}")


def run():
    log.info(f"Starting {SERVICE_NAME} on port {PORT}")
    send_heartbeat()
    uvicorn.run(app, host="0.0.0.0", port=PORT)


if __name__ == "__main__":
    run()