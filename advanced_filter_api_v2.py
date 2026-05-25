import logging
import os
import sys
import signal
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import requests
import uvicorn
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse

SERVICE_NAME = "advanced_filter_api"
SERVICE_PORT = 8777
PID_FILE = f"/tmp/{SERVICE_NAME}.pid"
LOG_FILE = f"/home/workspace/logs/{SERVICE_NAME}.log"
WRITE_SERVICE_URL = "http://localhost:8772"
QUERY_URL = "http://localhost:8772/query"
EXECUTE_URL = "http://localhost:8772/execute"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
LOG = logging.getLogger(SERVICE_NAME)

app = FastAPI(title="Advanced Filter API", version="2.0.0")

process_instance = None

VERDICT_TIERS = ["KNOWN_THREAT", "HIGH_RISK_ISOLATED", "CAUTION_LIMITED", 
                 "AMBER_UNVERIFIED", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED", "UNKNOWN"]

SIGNAL_TYPES = ["supply_chain", "community_signal", "temporal_stability",
                "permission_scope", "tool_description", "injection_resilience",
                "context_efficiency", "domain_trust", "registry_breadth"]


def check_single_instance():
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        old_pid = int(pid_path.read_text().strip())
        try:
            os.kill(old_pid, 0)
            LOG.error(f"Another instance running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            LOG.info(f"Stale PID file found, removing")
            pid_path.unlink()
    pid_path.write_text(str(os.getpid()))


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
    except Exception:
        pass


def signal_handler(signum, frame):
    LOG.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> list:
    try:
        response = requests.post(
            QUERY_URL,
            json={"sql": sql},
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        return result.get("rows", [])
    except Exception as e:
        LOG.error(f"Query failed: {e}")
        return []


def ws_write(table: str, rows: list):
    try:
        response = requests.post(
            WRITE_SERVICE_URL,
            json={"table": table, "rows": rows},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        LOG.error(f"Write failed: {e}")
        return {"ok": False, "error": str(e)}


def send_heartbeat():
    try:
        ws_write("service_health", [{
            "service": SERVICE_NAME,
            "last_heartbeat": utc_now_iso(),
            "status": "ok"
        }])
    except Exception:
        pass


def build_filter_query(
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
    verdicts: Optional[list] = None,
    signal_filters: Optional[dict] = None,
    limit: int = 100,
    offset: int = 0
) -> tuple[str, list]:
    conditions = []
    params = []
    
    if min_score is not None:
        conditions.append("trust_score >= ?")
        params.append(min_score)
    
    if max_score is not None:
        conditions.append("trust_score <= ?")
        params.append(max_score)
    
    if verdicts:
        verdict_placeholders = ",".join(["?" for _ in verdicts])
        conditions.append(f"verdict IN ({verdict_placeholders})")
        params.extend(verdicts)
    
    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)
    
    base_sql = f"""
    SELECT server_id, name, url, description, trust_score, verdict, registry_source, scan_count
    FROM mcp_server_registry
    {where_clause}
    ORDER BY trust_score DESC
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    
    return base_sql, params


def get_signal_scores_for_servers(server_ids: list) -> dict:
    if not server_ids:
        return {}
    
    ids_placeholder = ",".join([f"'{sid}'" for sid in server_ids])
    sql = f"""
    SELECT server_id, signal_name, score, evidence
    FROM mcp_signal_scores
    WHERE server_id IN ({ids_placeholder})
    ORDER BY server_id, signal_name
    """
    
    rows = ws_query(sql)
    signal_map = {}
    for row in rows:
        sid = row.get("server_id", "")
        if sid not in signal_map:
            signal_map[sid] = []
        signal_map[sid].append({
            "signal_name": row.get("signal_name", ""),
            "score": row.get("score", 0.0),
            "evidence": row.get("evidence", "")
        })
    return signal_map


def matches_signal_filters(signals: list, signal_filters: dict) -> bool:
    if not signal_filters:
        return True
    
    for sig_name, min_val in signal_filters.items():
        matching = [s for s in signals if s.get("signal_name") == sig_name]
        if not matching:
            return False
        if matching[0].get("score", 0) < min_val:
            return False
    return True


@app.get("/health")
def health():
    return JSONResponse({"status": "ok", "service": SERVICE_NAME, "version": "2.0.0"})


@app.get("/servers/filtered")
def get_filtered_servers(
    min_score: Optional[float] = Query(None, description="Minimum trust score"),
    max_score: Optional[float] = Query(None, description="Maximum trust score"),
    verdicts: Optional[str] = Query(None, description="Comma-separated verdict tiers"),
    supply_chain_min: Optional[float] = Query(None, description="Min supply_chain signal score"),
    community_min: Optional[float] = Query(None, description="Min community_signal score"),
    temporal_min: Optional[float] = Query(None, description="Min temporal_stability score"),
    permission_min: Optional[float] = Query(None, description="Min permission_scope score"),
    tool_desc_min: Optional[float] = Query(None, description="Min tool_description signal score"),
    injection_min: Optional[float] = Query(None, description="Min injection_resilience score"),
    context_min: Optional[float] = Query(None, description="Min context_efficiency score"),
    domain_min: Optional[float] = Query(None, description="Min domain_trust score"),
    registry_min: Optional[float] = Query(None, description="Min registry_breadth score"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    signal_filters = {}
    if supply_chain_min is not None:
        signal_filters["supply_chain"] = supply_chain_min
    if community_min is not None:
        signal_filters["community_signal"] = community_min
    if temporal_min is not None:
        signal_filters["temporal_stability"] = temporal_min
    if permission_min is not None:
        signal_filters["permission_scope"] = permission_min
    if tool_desc_min is not None:
        signal_filters["tool_description"] = tool_desc_min
    if injection_min is not None:
        signal_filters["injection_resilience"] = injection_min
    if context_min is not None:
        signal_filters["context_efficiency"] = context_min
    if domain_min is not None:
        signal_filters["domain_trust"] = domain_min
    if registry_min is not None:
        signal_filters["registry_breadth"] = registry_min
    
    parsed_verdicts = None
    if verdicts:
        parsed_verdicts = [v.strip() for v in verdicts.split(",")]
    
    sql, params = build_filter_query(
        min_score=min_score,
        max_score=max_score,
        verdicts=parsed_verdicts,
        signal_filters=signal_filters,
        limit=limit,
        offset=offset
    )
    
    servers = ws_query(sql)
    
    if signal_filters and servers:
        server_ids = [s.get("server_id", "") for s in servers]
        signal_map = get_signal_scores_for_servers(server_ids)
        
        filtered_servers = []
        for server in servers:
            sid = server.get("server_id", "")
            signals = signal_map.get(sid, [])
            if matches_signal_filters(signals, signal_filters):
                server_copy = dict(server)
                server_copy["signals"] = signals
                filtered_servers.append(server_copy)
        
        return JSONResponse({
            "servers": filtered_servers,
            "count": len(filtered_servers),
            "limit": limit,
            "offset": offset,
            "filters_applied": {
                "min_score": min_score,
                "max_score": max_score,
                "verdicts": parsed_verdicts,
                "signal_filters": signal_filters
            }
        })
    
    for server in servers:
        server["signals"] = signal_map.get(server.get("server_id", ""), [])
    
    return JSONResponse({
        "servers": servers,
        "count": len(servers),
        "limit": limit,
        "offset": offset,
        "filters_applied": {
            "min_score": min_score,
            "max_score": max_score,
            "verdicts": parsed_verdicts,
            "signal_filters": signal_filters
        }
    })


@app.get("/verdicts")
def get_verdicts():
    return JSONResponse({"verdicts": VERDICT_TIERS})


@app.get("/signals")
def get_signals():
    return JSONResponse({"signals": SIGNAL_TYPES})


def run():
    global process_instance
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    
    LOG.info(f"Starting {SERVICE_NAME} on port {SERVICE_PORT}")
    send_heartbeat()
    
    import threading
    def heartbeat_loop():
        while True:
            time.sleep(60)
            send_heartbeat()
    
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    
    uvicorn.run(app, host="0.0.0.0", port=SERVICE_PORT, log_level="info")


if __name__ == "__main__":
    run()