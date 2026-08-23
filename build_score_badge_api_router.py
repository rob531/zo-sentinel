import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException, Query

WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://localhost:8772")
QUERY_URL = os.environ.get("QUERY_SERVICE_URL", "http://localhost:8772")
EXECUTE_URL = os.environ.get("EXECUTE_SERVICE_URL", "http://localhost:8772")

SERVICE_NAME = "score_badge_api"
PORT = 8790

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.FileHandler(f"/home/workspace/logs/{SERVICE_NAME}.log"), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/score-badge", tags=["score-badge"])


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    response = requests.post(QUERY_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("rows", [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {"table": table, "rows": rows, "wait": True}
    response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()


def score_to_color(score: float) -> str:
    if score >= 80:
        return "#22c55e"
    elif score >= 60:
        return "#84cc16"
    elif score >= 40:
        return "#eab308"
    elif score >= 20:
        return "#f97316"
    else:
        return "#ef4444"


def score_to_label(score: float) -> str:
    if score >= 80:
        return "Trusted"
    elif score >= 60:
        return "Likely Safe"
    elif score >= 40:
        return "Caution"
    elif score >= 20:
        return "Suspicious"
    else:
        return "High Risk"


def get_server_verdict(server_id: str) -> Optional[Dict[str, Any]]:
    sql = """
    SELECT server_id, name, verdict, trust_score, risk_tier
    FROM mcp_server_registry
    WHERE server_id = ?
    """
    rows = ws_query(sql, [server_id])
    return rows[0] if rows else None


def get_signal_scores(server_id: str) -> List[Dict[str, Any]]:
    sql = """
    SELECT server_id, signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = ?
    ORDER BY signal_name
    """
    return ws_query(sql, [server_id])


def get_all_servers_paginated(
    limit: int = 50,
    offset: int = 0,
    verdict_filter: Optional[str] = None,
    min_score: Optional[float] = None,
    max_score: Optional[float] = None,
) -> Dict[str, Any]:
    conditions = []
    params: List[Any] = []

    if verdict_filter:
        conditions.append("verdict = ?")
        params.append(verdict_filter)

    if min_score is not None:
        conditions.append("trust_score >= ?")
        params.append(min_score)

    if max_score is not None:
        conditions.append("trust_score <= ?")
        params.append(max_score)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    count_sql = f"SELECT COUNT(*) as total FROM mcp_server_registry WHERE {where_clause}"
    count_rows = ws_query(count_sql, params)
    total = count_rows[0]["total"] if count_rows else 0

    data_sql = f"""
    SELECT server_id, name, url, verdict, trust_score, risk_tier, first_seen, last_seen
    FROM mcp_server_registry
    WHERE {where_clause}
    ORDER BY trust_score DESC NULLS LAST
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = ws_query(data_sql, params)

    return {"total": total, "limit": limit, "offset": offset, "servers": rows}


def get_server_badge(server_id: str) -> Dict[str, Any]:
    verdict_data = get_server_verdict(server_id)
    if not verdict_data:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    signal_scores = get_signal_scores(server_id)

    trust_score = verdict_data.get("trust_score", 0.0) or 0.0

    badge_data = {
        "server_id": server_id,
        "name": verdict_data.get("name", ""),
        "verdict": verdict_data.get("verdict", "UNKNOWN"),
        "trust_score": trust_score,
        "color": score_to_color(trust_score),
        "label": score_to_label(trust_score),
        "risk_tier": verdict_data.get("risk_tier", ""),
        "signals": [
            {
                "signal_name": s.get("signal_name", ""),
                "score": s.get("score", 0.0) or 0.0,
                "evidence": s.get("evidence", ""),
                "scored_at": s.get("scored_at", ""),
            }
            for s in signal_scores
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    return badge_data


@router.get("/server/{server_id}")
def get_badge(server_id: str) -> Dict[str, Any]:
    try:
        return get_server_badge(server_id)
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Error getting badge for {server_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/servers")
def list_badges(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    verdict: Optional[str] = None,
    min_score: Optional[float] = Query(default=None, ge=0, le=100),
    max_score: Optional[float] = Query(default=None, ge=0, le=100),
) -> Dict[str, Any]:
    try:
        return get_all_servers_paginated(
            limit=limit,
            offset=offset,
            verdict_filter=verdict,
            min_score=min_score,
            max_score=max_score,
        )
    except Exception as e:
        log.error(f"Error listing badges: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/verdict/{verdict}")
def list_by_verdict(
    verdict: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> Dict[str, Any]:
    try:
        return get_all_servers_paginated(
            limit=limit,
            offset=offset,
            verdict_filter=verdict.upper(),
        )
    except Exception as e:
        log.error(f"Error listing by verdict {verdict}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
def get_badge_stats() -> Dict[str, Any]:
    try:
        sql = """
        SELECT
            verdict,
            COUNT(*) as count,
            AVG(trust_score) as avg_score,
            MIN(trust_score) as min_score,
            MAX(trust_score) as max_score
        FROM mcp_server_registry
        WHERE verdict IS NOT NULL
        GROUP BY verdict
        ORDER BY count DESC
        """
        rows = ws_query(sql)

        sql_total = "SELECT COUNT(*) as total, AVG(trust_score) as global_avg FROM mcp_server_registry"
        total_rows = ws_query(sql_total)
        total_data = total_rows[0] if total_rows else {"total": 0, "global_avg": 0}

        return {
            "total": total_data.get("total", 0),
            "global_avg_score": round(total_data.get("global_avg") or 0, 2),
            "by_verdict": [
                {
                    "verdict": r.get("verdict", ""),
                    "count": r.get("count", 0),
                    "avg_score": round(r.get("avg_score") or 0, 2),
                    "min_score": r.get("min_score"),
                    "max_score": r.get("max_score"),
                }
                for r in rows
            ],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        log.error(f"Error getting badge stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/color/{score}")
def preview_color(score: float = Query(..., ge=0, le=100)) -> Dict[str, Any]:
    return {
        "score": score,
        "color": score_to_color(score),
        "label": score_to_label(score),
    }


@router.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok", "service": SERVICE_NAME}


if __name__ == "__main__":
    import uvicorn

    from fastapi import FastAPI

    app = FastAPI(title="Score Badge API")
    app.include_router(router)

    uvicorn.run(app, host="0.0.0.0", port=PORT)