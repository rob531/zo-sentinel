import os
import sys
import json
import time
import logging
import threading
import requests
from datetime import datetime, timezone
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

SERVICE_NAME = "pathway_api"
SERVICE_PORT = 8782
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = "http://127.0.0.1:8772/query"
PID_FILE = "/home/workspace/logs/pathway_api.lock"
LOG_FILE = "/home/workspace/logs/pathway_api.log"
HEARTBEAT_INTERVAL = 30

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
LOG = logging.getLogger(SERVICE_NAME)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

last_query_ms = 0.0

def ws_query(sql: str) -> list:
    global last_query_ms
    start = time.time()
    try:
        resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        last_query_ms = (time.time() - start) * 1000
        return data.get("rows", [])
    except Exception as e:
        LOG.error(f"Query failed: {e}")
        last_query_ms = (time.time() - start) * 1000
        return []

def ws_write(table: str, rows: dict) -> bool:
    try:
        resp = requests.post(f"{WRITE_SERVICE_URL}/write", json={"table": table, "rows": rows}, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        LOG.error(f"Write failed: {e}")
        return False

def send_heartbeat():
    now = datetime.now(timezone.utc).isoformat()
    ws_write("service_health", {"service": SERVICE_NAME, "last_heartbeat": now})

def heartbeat_loop():
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

def check_single_instance():
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    if os.path.exists(PID_FILE):
        try:
            with open(PID_FILE, 'r') as f:
                old_pid = int(f.read().strip())
            if old_pid > 0 and os.path.exists(f"/proc/{old_pid}"):
                LOG.error(f"Another instance running with PID {old_pid}")
                sys.exit(1)
        except (ValueError, ProcessLookupError):
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def signal_handler(signum, frame):
    LOG.info(f"Received signal {signum}, shutting down")
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
    sys.exit(0)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "port": SERVICE_PORT,
        "last_query_ms": round(last_query_ms, 2)
    }

@app.get("/api/pathway/totals")
async def get_totals():
    rows = ws_query("SELECT COUNT(*) as total FROM mcp_server_registry")
    total = 0
    if rows:
        total = rows[0].get("total", 0) or 0
    return {"total": total}

@app.get("/api/pathway/funnel")
async def get_funnel():
    discovered = ws_query("SELECT COUNT(*) as cnt FROM mcp_discovery_candidates")
    promoted = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    fingerprinted = ws_query("SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_fingerprints")
    scored = ws_query("SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_signal_scores")
    enriched = ws_query("SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_signal_enrichments")
    attested = ws_query("SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_attestations")

    return {
        "discovered": discovered[0].get("cnt", 0) if discovered else 0,
        "promoted": promoted[0].get("cnt", 0) if promoted else 0,
        "fingerprinted": fingerprinted[0].get("cnt", 0) if fingerprinted else 0,
        "scored": scored[0].get("cnt", 0) if scored else 0,
        "enriched": enriched[0].get("cnt", 0) if enriched else 0,
        "attested": attested[0].get("cnt", 0) if attested else 0
    }

def compute_hourly_counts(rows: list, hour_key: str, count_key: str) -> list:
    now = datetime.now(timezone.utc)
    hour_buckets = {}
    for i in range(24):
        h = now.replace(minute=0, second=0, microsecond=0) - (23 - i) * 3600
        hour_buckets[h.isoformat()] = 0
    for row in rows:
        h_val = row.get(hour_key, "")
        if h_val:
            try:
                dt = datetime.fromisoformat(h_val.replace('Z', '+00:00'))
                h_key = dt.replace(minute=0, second=0, microsecond=0).isoformat()
                if h_key in hour_buckets:
                    hour_buckets[h_key] = row.get(count_key, 0) or 0
            except Exception:
                pass
    return [hour_buckets[k] for k in sorted(hour_buckets.keys())]

def compute_current_rate(counts: list) -> float:
    total = sum(counts)
    return round(total / 24.0, 2)

@app.get("/api/pathway/velocity")
async def get_velocity():
    registry_rows = ws_query(
        "SELECT date_trunc('hour', first_seen) as h, COUNT(*) as cnt "
        "FROM mcp_server_registry WHERE first_seen > now() - INTERVAL 24 HOUR "
        "GROUP BY h ORDER BY h"
    )
    fingerprint_rows = ws_query(
        "SELECT date_trunc('hour', computed_at) as h, COUNT(*) as cnt "
        "FROM mcp_fingerprints WHERE computed_at > now() - INTERVAL 24 HOUR "
        "GROUP BY h ORDER BY h"
    )
    signal_rows = ws_query(
        "SELECT date_trunc('hour', scored_at) as h, COUNT(*) as cnt "
        "FROM mcp_signal_scores WHERE scored_at > now() - INTERVAL 24 HOUR "
        "GROUP BY h ORDER BY h"
    )

    registry_counts = compute_hourly_counts(registry_rows, "h", "cnt")
    fingerprint_counts = compute_hourly_counts(fingerprint_rows, "h", "cnt")
    signal_counts = compute_hourly_counts(signal_rows, "h", "cnt")

    return {
        "registry_velocity": registry_counts,
        "registry_current": compute_current_rate(registry_counts),
        "fingerprint_velocity": fingerprint_counts,
        "fingerprint_current": compute_current_rate(fingerprint_counts),
        "signal_velocity": signal_counts,
        "signal_current": compute_current_rate(signal_counts)
    }

@app.get("/api/pathway/eta")
async def get_eta():
    MAX_TARGET = 20000
    MAX_DAYS = 36500

    registry_rows = ws_query(
        "SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE first_seen > now() - INTERVAL 24 HOUR"
    )
    fingerprint_rows = ws_query(
        "SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_fingerprints WHERE computed_at > now() - INTERVAL 24 HOUR"
    )
    attestation_rows = ws_query(
        "SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_attestations WHERE created_at > now() - INTERVAL 24 HOUR"
    )

    registry_hourly = registry_rows[0].get("cnt", 0) if registry_rows else 0
    fingerprint_hourly = fingerprint_rows[0].get("cnt", 0) if fingerprint_rows else 0
    attestation_hourly = attestation_rows[0].get("cnt", 0) if attestation_rows else 0

    current_registry = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry")
    current_fingerprint = ws_query("SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_fingerprints")
    current_attestation = ws_query("SELECT COUNT(DISTINCT server_id) as cnt FROM mcp_attestations")

    cur_reg = current_registry[0].get("cnt", 0) if current_registry else 0
    cur_fing = current_fingerprint[0].get("cnt", 0) if current_fingerprint else 0
    cur_att = current_attestation[0].get("cnt", 0) if current_attestation else 0

    hourly_rate_registry = max(0.001, registry_hourly / 24.0)
    hourly_rate_fingerprint = max(0.001, fingerprint_hourly / 24.0)
    hourly_rate_attestation = max(0.001, attestation_hourly / 24.0)

    reg_eta = min(MAX_DAYS, (MAX_TARGET - cur_reg) / (hourly_rate_registry * 24))
    fing_eta = min(MAX_DAYS, (MAX_TARGET - cur_fing) / (hourly_rate_fingerprint * 24))
    att_eta = min(MAX_DAYS, (MAX_TARGET - cur_att) / (hourly_rate_attestation * 24))

    return {
        "registry_eta_days": round(reg_eta, 1),
        "registry_eta_percent": round(min(100.0, cur_reg / MAX_TARGET * 100), 2),
        "fingerprint_eta_days": round(fing_eta, 1),
        "fingerprint_eta_percent": round(min(100.0, cur_fing / MAX_TARGET * 100), 2),
        "attestation_eta_days": round(att_eta, 1),
        "attestation_eta_percent": round(min(100.0, cur_att / MAX_TARGET * 100), 2)
    }

def run():
    check_single_instance()
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()

    LOG.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    uvicorn.run(app, host="127.0.0.1", port=SERVICE_PORT, log_level="info")

if __name__ == "__main__":
    run()