from fastapi import FastAPI, HTTPException
import uvicorn
import httpx
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

app = FastAPI(title="Score Timeline API")

PORT = 8782
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
start_time = time.time()


async def query_db(sql: str) -> List[Dict[str, Any]]:
    """Query the write service database."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                f"{WRITE_SERVICE_URL}/query",
                json={"sql": sql},
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()
            return data.get("rows", [])
        except httpx.HTTPError as e:
            raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")


@app.get("/health")
async def health():
    uptime = int(time.time() - start_time)
    return {"status": "ok", "service": "score_timeline_api", "uptime": uptime}


@app.get("/api/v1/servers/{server_id}/signal_timeline")
async def get_server_signal_timeline(
    server_id: str,
    signal_name: Optional[str] = None,
    days: int = 30,
    limit: int = 500
) -> Dict[str, Any]:
    """
    Retrieve signal score history for a server over time.
    
    Args:
        server_id: The server identifier
        signal_name: Optional filter for specific signal (e.g., 'code_quality', 'security')
        days: Number of days to look back (default 30)
        limit: Maximum number of records to return
    
    Returns:
        Timeline of signal scores with timestamps and evidence
    """
    conditions = f"server_id = '{server_id}' AND scored_at >= NOW() - INTERVAL '{days} days'"
    
    if signal_name:
        conditions += f" AND signal_name = '{signal_name}'"
    
    sql = f"""
        SELECT 
            server_id,
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE {conditions}
        ORDER BY scored_at DESC
        LIMIT {limit}
    """
    
    try:
        rows = await query_db(sql)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )
    
    return {
        "server_id": server_id,
        "signal_name": signal_name,
        "days": days,
        "count": len(rows),
        "timeline": rows
    }


@app.get("/api/v1/servers/{server_id}/verdict_timeline")
async def get_server_verdict_timeline(
    server_id: str,
    days: int = 90
) -> Dict[str, Any]:
    """
    Retrieve verdict history for a server from audit log.
    """
    sql = f"""
        SELECT 
            event_type,
            action,
            target_server_id,
            outcome,
            timestamp,
            details_json
        FROM audit_log
        WHERE target_server_id = '{server_id}'
        AND event_type IN ('verdict_change', 'server_review', 'submission_verdict')
        AND timestamp >= NOW() - INTERVAL '{days} days'
        ORDER BY timestamp DESC
    """
    
    try:
        rows = await query_db(sql)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )
    
    return {
        "server_id": server_id,
        "days": days,
        "count": len(rows),
        "verdict_history": rows
    }


@app.get("/api/v1/signals/{signal_name}/timeline")
async def get_signal_timeline(
    signal_name: str,
    days: int = 30,
    limit: int = 1000
) -> Dict[str, Any]:
    """
    Retrieve score timeline for a specific signal across all servers.
    """
    sql = f"""
        SELECT 
            server_id,
            signal_name,
            score,
            evidence,
            scored_at
        FROM mcp_signal_scores
        WHERE signal_name = '{signal_name}'
        AND scored_at >= NOW() - INTERVAL '{days} days'
        ORDER BY scored_at DESC
        LIMIT {limit}
    """
    
    try:
        rows = await query_db(sql)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )
    
    return {
        "signal_name": signal_name,
        "days": days,
        "count": len(rows),
        "timeline": rows
    }


@app.get("/api/v1/timeline/summary")
async def get_timeline_summary(
    days: int = 7,
    limit: int = 100
) -> Dict[str, Any]:
    """
    Get a summary of recent signal scoring activity.
    """
    sql = f"""
        SELECT 
            signal_name,
            COUNT(*) as score_count,
            AVG(score) as avg_score,
            MIN(scored_at) as first_seen,
            MAX(scored_at) as last_seen
        FROM mcp_signal_scores
        WHERE scored_at >= NOW() - INTERVAL '{days} days'
        GROUP BY signal_name
        ORDER BY score_count DESC
        LIMIT {limit}
    """
    
    try:
        rows = await query_db(sql)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Database query failed: {str(e)}"
        )
    
    return {
        "days": days,
        "count": len(rows),
        "signal_summary": rows
    }


def run():
    uvicorn.run(app, host='127.0.0.1', port=PORT)


if __name__ == '__main__':
    run()