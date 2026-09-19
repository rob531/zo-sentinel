import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

SERVICE_NAME = "axis_drift_scoring_consumer_router"
SERVICE_PORT = 8786
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_SERVICE_URL = "http://localhost:8772/query"
EXECUTE_SERVICE_URL = "http://localhost:8772/execute"

LOG_DIR = Path("/home/workspace/logs")
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(str(LOG_FILE)), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

app = FastAPI(title=SERVICE_NAME, version="1.0.0")

_PROCESS_RUNNING = True
_start_time = datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def get_uptime_seconds() -> float:
    return (datetime.now(timezone.utc) - _start_time.replace(tzinfo=None)).total_seconds()


def check_single_instance() -> None:
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        old_pid = int(pid_file.read_text().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log.warning(f"Stale PID file found, removing")
            pid_file.unlink()
    pid_file.write_text(str(os.getpid()))


def remove_pid_file() -> None:
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception as e:
        log.error(f"Error removing PID file: {e}")


def signal_handler(signum, frame):
    global _PROCESS_RUNNING
    sig_name = signal.Signals(signum).name
    log.info(f"Received {sig_name}, shutting down gracefully")
    _PROCESS_RUNNING = False
    remove_pid_file()
    sys.exit(0)


def ws_query(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"WriteService query failed: {e}")
        return {"rows": [], "error": str(e)}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        resp = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows, "wait": True},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"WriteService write failed: {e}")
        return {"ok": False, "error": str(e)}


def ws_execute(sql: str) -> Dict[str, Any]:
    try:
        resp = requests.post(
            EXECUTE_SERVICE_URL,
            json={"sql": sql},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f"WriteService execute failed: {e}")
        return {"ok": False, "error": str(e)}


def send_heartbeat() -> None:
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "status": "running",
            "last_heartbeat": utc_now_iso(),
            "meta": {"uptime_seconds": get_uptime_seconds()}
        }])
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")


class AxisDriftEvent(BaseModel):
    server_id: str
    signal_type: str
    previous_score: float
    current_score: float
    drift_delta: float
    computed_at: Optional[str] = None


class AxisDriftProcessRequest(BaseModel):
    server_ids: Optional[List[str]] = None
    lookback_hours: int = 24
    drift_threshold: float = 0.1


class AxisDriftResult(BaseModel):
    server_id: str
    signal_type: str
    drift_delta: float
    severity: str
    processed_at: str


@app.on_event("startup")
async def startup():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f"{SERVICE_NAME} starting on port {SERVICE_PORT}")
    send_heartbeat()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "uptime_seconds": get_uptime_seconds(),
        "timestamp": utc_now_iso()
    }


@app.get("/")
async def root():
    return {
        "service": SERVICE_NAME,
        "version": "1.0.0",
        "endpoints": [
            "GET /health",
            "POST /process",
            "POST /drift/summary",
            "GET /drift/server/{server_id}"
        ]
    }


@app.post("/process")
async def process_axis_drift(request: AxisDriftProcessRequest, background_tasks: BackgroundTasks):
    try:
        lookback_hours = request.lookback_hours
        drift_threshold = request.drift_threshold
        
        sql = f"""
        SELECT 
            server_id,
            signal_type,
            score as current_score,
            scored_at
        FROM mcp_signal_scores
        WHERE scored_at >= now() - INTERVAL '{lookback_hours} hours'
        """
        
        if request.server_ids:
            ids_list = "', '".join(request.server_ids)
            sql += f" AND server_id IN ('{ids_list}')"
        
        result = ws_query(sql)
        rows = result.get("rows", [])
        
        if not rows:
            return {"processed": 0, "drift_events": [], "message": "No signal scores found"}
        
        drift_events = []
        server_signals: Dict[str, List[Dict]] = {}
        
        for row in rows:
            server_id = row.get("server_id")
            signal_type = row.get("signal_type")
            if server_id not in server_signals:
                server_signals[server_id] = []
            server_signals[server_id].append(row)
        
        for server_id, signals in server_signals.items():
            if len(signals) < 2:
                continue
            
            signals.sort(key=lambda x: x.get("scored_at", ""), reverse=True)
            current = signals[0]
            previous = signals[1] if len(signals) > 1 else None
            
            if not previous:
                continue
            
            current_score = float(current.get("current_score", 0))
            previous_score = float(previous.get("current_score", 0))
            drift_delta = abs(current_score - previous_score)
            
            if drift_delta >= drift_threshold:
                severity = "low"
                if drift_delta >= 0.3:
                    severity = "high"
                elif drift_delta >= 0.2:
                    severity = "medium"
                
                event = AxisDriftEvent(
                    server_id=server_id,
                    signal_type=current.get("signal_type", "unknown"),
                    previous_score=previous_score,
                    current_score=current_score,
                    drift_delta=drift_delta,
                    computed_at=utc_now_iso()
                )
                drift_events.append(event)
        
        background_tasks.add_task(store_drift_events, drift_events)
        
        return {
            "processed": len(rows),
            "drift_events_count": len(drift_events),
            "drift_threshold": drift_threshold,
            "lookback_hours": lookback_hours,
            "timestamp": utc_now_iso()
        }
        
    except Exception as e:
        log.error(f"Error processing axis drift: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def store_drift_events(events: List[AxisDriftEvent]) -> None:
    try:
        if not events:
            return
        
        ws_execute("""
        CREATE TABLE IF NOT EXISTS axis_drift_events (
            event_id VARCHAR PRIMARY KEY,
            server_id VARCHAR,
            signal_type VARCHAR,
            previous_score DOUBLE,
            current_score DOUBLE,
            drift_delta DOUBLE,
            severity VARCHAR,
            computed_at TIMESTAMPTZ
        )
        """)
        
        for event in events:
            import hashlib
            event_id_input = f"{event.server_id}:{event.signal_type}:{utc_now_iso()}"
            event_id = hashlib.sha256(event_id_input.encode()).hexdigest()[:16]
            
            severity = "low"
            if event.drift_delta >= 0.3:
                severity = "high"
            elif event.drift_delta >= 0.2:
                severity = "medium"
            
            row = {
                "event_id": event_id,
                "server_id": event.server_id,
                "signal_type": event.signal_type,
                "previous_score": event.previous_score,
                "current_score": event.current_score,
                "drift_delta": event.drift_delta,
                "severity": severity,
                "computed_at": utc_now_iso()
            }
            ws_write("axis_drift_events", [row])
        
        log.info(f"Stored {len(events)} drift events")
        
    except Exception as e:
        log.error(f"Error storing drift events: {e}")


@app.post("/drift/summary")
async def get_drift_summary(lookback_hours: int = 24):
    try:
        sql = f"""
        SELECT 
            severity,
            COUNT(*) as event_count,
            AVG(drift_delta) as avg_drift_delta,
            COUNT(DISTINCT server_id) as affected_servers
        FROM axis_drift_events
        WHERE computed_at >= now() - INTERVAL '{lookback_hours} hours'
        GROUP BY severity
        """
        
        result = ws_query(sql)
        rows = result.get("rows", [])
        
        total_events = sum(r.get("event_count", 0) for r in rows)
        total_servers = sum(r.get("affected_servers", 0) for r in rows)
        
        return {
            "lookback_hours": lookback_hours,
            "total_events": total_events,
            "affected_servers": total_servers,
            "by_severity": rows,
            "timestamp": utc_now_iso()
        }
        
    except Exception as e:
        log.error(f"Error getting drift summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/drift/server/{server_id}")
async def get_server_drift(server_id: str, lookback_hours: int = 168):
    try:
        sql = f"""
        SELECT 
            event_id,
            signal_type,
            previous_score,
            current_score,
            drift_delta,
            severity,
            computed_at
        FROM axis_drift_events
        WHERE server_id = '{server_id}'
          AND computed_at >= now() - INTERVAL '{lookback_hours} hours'
        ORDER BY computed_at DESC
        LIMIT 100
        """
        
        result = ws_query(sql)
        rows = result.get("rows", [])
        
        if not rows:
            raise HTTPException(status_code=404, detail=f"No drift events found for server {server_id}")
        
        return {
            "server_id": server_id,
            "lookback_hours": lookback_hours,
            "event_count": len(rows),
            "events": rows,
            "timestamp": utc_now_iso()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting server drift: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def run():
    log.info(f"Starting {SERVICE_NAME}")
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        uvicorn.run(
            app,
            host="0.0.0.0",
            port=SERVICE_PORT,
            log_level="info"
        )
    except Exception as e:
        log.error(f"Failed to start service: {e}")
        remove_pid_file()
        sys.exit(1)
    finally:
        remove_pid_file()


if __name__ == "__main__":
    run()