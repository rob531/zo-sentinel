#!/usr/bin/env python3
"""
bulk_assess_api.py -- ZO-SENTINEL Phase 6
FastAPI Bulk Assessment API on port 8784.
Triggers scoring for multiple MCPs, tracks jobs, bulk imports, CSV export.
"""
import os
import uuid
import csv
import io
import time
import logging
import requests
import threading
import fcntl
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.responses import StreamingResponse, JSONResponse
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)

SERVICE_NAME = "bulk_assess_api"
PORT = 8784
WRITE_SERVICE = "http://127.0.0.1:8772"
EXECUTE_URL = "http://127.0.0.1:8772/execute"
QUERY_URL = "http://127.0.0.1:8772/query"
HEARTBEAT_INTERVAL = 30
MAX_MCPS_PER_REQUEST = 20
JOB_TTL_SECONDS = 3600

jobs: Dict[str, Dict[str, Any]] = {}
jobs_lock = threading.Lock()


def check_single_instance(name: str) -> bool:
    lock_file = f"/tmp/{name}.lock"
    try:
        with open(lock_file, "w") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            f.write(str(os.getpid()))
            return True
    except (IOError, OSError):
        log.error(f"Another instance of {name} is already running")
        return False


def get_write_url() -> str:
    return WRITE_SERVICE


def ws_query(sql: str) -> list:
    try:
        r = requests.post(f"{QUERY_URL}", json={"sql": sql}, timeout=30)
        if r.status_code == 200:
            return r.json().get("rows", [])
        log.warning(f"ws_query failed: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        log.error(f"ws_query error: {e}")
    return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    try:
        payload = {"table": table, "rows": rows}
        r = requests.post(f"{WRITE_SERVICE}/write", json=payload, timeout=30)
        if r.status_code == 200:
            return True
        log.warning(f"ws_write failed: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        log.error(f"ws_write error: {e}")
    return False


def ws_execute(sql: str) -> bool:
    try:
        r = requests.post(f"{EXECUTE_URL}", json={"sql": sql}, timeout=30)
        if r.status_code == 200:
            return True
        log.warning(f"ws_execute failed: {r.status_code} - {r.text[:200]}")
    except Exception as e:
        log.error(f"ws_execute error: {e}")
    return False


def send_heartbeat() -> bool:
    try:
        payload = {
            "table": "service_health",
            "rows": {
                "service": SERVICE_NAME,
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "status": "running"
            }
        }
        r = requests.post(f"{WRITE_SERVICE}/write", json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")
        return False


def cleanup_expired_jobs():
    now = time.time()
    expired = []
    with jobs_lock:
        for job_id, job in jobs.items():
            if now - job.get("created_at", 0) > JOB_TTL_SECONDS:
                expired.append(job_id)
        for job_id in expired:
            del jobs[job_id]
    if expired:
        log.info(f"Cleaned up {len(expired)} expired jobs")


def process_bulk_assess(job_id: str, mcp_list: List[str]):
    try:
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "running"
        
        results = []
        for mcp in mcp_list:
            mcp_clean = mcp.strip()
            if not mcp_clean:
                continue
            
            row = ws_query(
                f"SELECT server_id, name, url, description, verdict, trust_score, "
                f"verdict_reasoning, confidence, last_assessed "
                f"FROM mcp_server_registry "
                f"WHERE name ILIKE '%{mcp_clean}%' OR url ILIKE '%{mcp_clean}%' "
                f"OR server_id ILIKE '%{mcp_clean}%' "
                f"ORDER BY last_assessed DESC NULLS LAST LIMIT 1"
            )
            
            if row:
                r = row[0]
                results.append({
                    "mcp": mcp_clean,
                    "server_id": r.get("server_id"),
                    "name": r.get("name"),
                    "verdict": r.get("verdict", "UNKNOWN"),
                    "trust_score": r.get("trust_score"),
                    "confidence": r.get("confidence"),
                    "last_assessed": r.get("last_assessed")
                })
            else:
                results.append({
                    "mcp": mcp_clean,
                    "server_id": None,
                    "name": None,
                    "verdict": "INSUFFICIENT",
                    "trust_score": None,
                    "confidence": None,
                    "last_assessed": None
                })
        
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "complete"
                jobs[job_id]["results"] = results
                jobs[job_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
        
        log.info(f"Job {job_id} completed with {len(results)} results")
        
    except Exception as e:
        log.error(f"Job {job_id} failed: {e}")
        with jobs_lock:
            if job_id in jobs:
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["error"] = str(e)


def create_bulk_assess_jobs_table():
    sql = """
    CREATE TABLE IF NOT EXISTS bulk_assess_jobs (
        id BIGINT PRIMARY KEY,
        job_id VARCHAR UNIQUE NOT NULL,
        mcp_list TEXT,
        status VARCHAR,
        submitted_at TIMESTAMPTZ DEFAULT now(),
        completed_at TIMESTAMPTZ,
        results_count INTEGER DEFAULT 0
    )
    """
    ws_execute(sql)


def create_bulk_imports_table():
    sql = """
    CREATE TABLE IF NOT EXISTS bulk_imports (
        id BIGINT PRIMARY KEY,
        import_id VARCHAR UNIQUE NOT NULL,
        servers_count INTEGER,
        status VARCHAR,
        imported_at TIMESTAMPTZ DEFAULT now()
    )
    """
    ws_execute(sql)


def generate_csv_stream() -> io.StringIO:
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow([
        "server_id", "name", "registry_source", "url", "description",
        "trust_score", "verdict", "verdict_reasoning", "confidence",
        "last_assessed", "first_seen", "last_seen", "scan_count"
    ])
    
    offset = 0
    batch_size = 500
    
    while True:
        rows = ws_query(
            f"SELECT server_id, name, registry_source, url, description, "
            f"trust_score, verdict, verdict_reasoning, confidence, "
            f"last_assessed, first_seen, last_seen, scan_count "
            f"FROM mcp_server_registry "
            f"ORDER BY last_seen DESC NULLS LAST "
            f"LIMIT {batch_size} OFFSET {offset}"
        )
        
        if not rows:
            break
        
        for row in rows:
            writer.writerow([
                row.get("server_id", ""),
                row.get("name", ""),
                row.get("registry_source", ""),
                row.get("url", ""),
                row.get("description", ""),
                row.get("trust_score", ""),
                row.get("verdict", ""),
                row.get("verdict_reasoning", ""),
                row.get("confidence", ""),
                row.get("last_assessed", ""),
                row.get("first_seen", ""),
                row.get("last_seen", ""),
                row.get("scan_count", "")
            ])
        
        offset += batch_size
        
        if len(rows) < batch_size:
            break
    
    output.seek(0)
    return output


app = FastAPI(title="ZO-SENTINEL Bulk Assessment API", version="1.0.0")


@app.on_event("startup")
async def startup():
    if not check_single_instance(SERVICE_NAME):
        raise RuntimeError(f"Another instance of {SERVICE_NAME} is running")
    
    create_bulk_assess_jobs_table()
    create_bulk_imports_table()
    send_heartbeat()
    log.info(f"{SERVICE_NAME} started on port {PORT}")


@app.on_event("shutdown")
async def shutdown():
    log.info(f"{SERVICE_NAME} shutting down")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "port": PORT,
        "jobs_tracked": len(jobs)
    }


@app.post("/v1/bulk/assess")
def bulk_assess(mcps: List[str], background_tasks: BackgroundTasks):
    if not mcps:
        raise HTTPException(status_code=400, detail="mcps list cannot be empty")
    
    if len(mcps) > MAX_MCPS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {MAX_MCPS_PER_REQUEST} MCPs per request"
        )
    
    job_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    with jobs_lock:
        jobs[job_id] = {
            "status": "pending",
            "mcps": mcps,
            "results": [],
            "created_at": time.time(),
            "submitted_at": now.isoformat()
        }
    
    ws_write("bulk_assess_jobs", [{
        "job_id": job_id,
        "mcp_list": ",".join(mcps[:10]),
        "status": "pending",
        "submitted_at": now.isoformat()
    }])
    
    background_tasks.add_task(process_bulk_assess, job_id, mcps)
    
    log.info(f"Bulk assess job {job_id} created for {len(mcps)} MCPs")
    
    return {
        "submitted": len(mcps),
        "job_id": job_id,
        "status": "pending",
        "message": f"Assessment job created. Poll /v1/bulk/status/{job_id} for results."
    }


@app.get("/v1/bulk/status/{job_id}")
def bulk_status(job_id: str):
    with jobs_lock:
        if job_id not in jobs:
            raise HTTPException(status_code=404, detail="Job not found")
        job = jobs[job_id]
    
    response = {
        "job_id": job_id,
        "status": job["status"],
        "submitted_at": job.get("submitted_at")
    }
    
    if job["status"] == "complete":
        response["results"] = job.get("results", [])
        response["completed_at"] = job.get("completed_at")
        response["total"] = len(job.get("results", []))
    elif job["status"] == "failed":
        response["error"] = job.get("error", "Unknown error")
    
    return response


@app.post("/v1/bulk/import")
def bulk_import(servers: List[Dict[str, Any]]):
    if not servers:
        raise HTTPException(status_code=400, detail="servers list cannot be empty")
    
    if len(servers) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 servers per import")
    
    import_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    rows_to_insert = []
    for idx, server in enumerate(servers):
        name = server.get("name", "").strip()
        url = server.get("url", "").strip()
        description = server.get("description", "").strip()
        
        if not name and not url:
            continue
        
        server_id = f"import_{import_id[:8]}_{idx}"
        
        existing = ws_query(
            f"SELECT id FROM mcp_server_registry WHERE url = '{url}' LIMIT 1"
        )
        
        if existing:
            ws_execute(
                f"UPDATE mcp_server_registry SET "
                f"name = COALESCE(NULLIF('{name}', ''), name), "
                f"description = COALESCE(NULLIF('{description}', ''), description), "
                f"last_seen = '{now.isoformat()}' "
                f"WHERE url = '{url}'"
            )
        else:
            rows_to_insert.append({
                "server_id": server_id,
                "name": name or None,
                "url": url or None,
                "description": description or None,
                "registry_source": "bulk_import",
                "first_seen": now.isoformat(),
                "last_seen": now.isoformat(),
                "trust_score": None,
                "verdict": "PENDING",
                "confidence": None,
                "last_assessed": None
            })
    
    success_count = 0
    if rows_to_insert:
        if ws_write("mcp_server_registry", rows_to_insert):
            success_count = len(rows_to_insert)
    
    ws_write("bulk_imports", [{
        "import_id": import_id,
        "servers_count": success_count,
        "status": "complete",
        "imported_at": now.isoformat()
    }])
    
    log.info(f"Bulk import {import_id}: {success_count} servers imported")
    
    return {
        "import_id": import_id,
        "imported": success_count,
        "skipped": len(servers) - success_count,
        "status": "complete"
    }


@app.get("/v1/export/csv")
def export_csv():
    def generate():
        csv_stream = generate_csv_stream()
        while True:
            chunk = csv_stream.read(4096)
            if not chunk:
                break
            yield chunk
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"zo_sentinel_registry_{timestamp}.csv"
    
    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


def heartbeat_loop():
    while True:
        try:
            send_heartbeat()
            cleanup_expired_jobs()
        except Exception as e:
            log.error(f"Heartbeat error: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    import uvicorn
    from threading import Thread
    
    heartbeat_thread = Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info")


if __name__ == "__main__":
    run()