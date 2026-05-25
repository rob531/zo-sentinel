import logging
import math
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

SERVICE_NAME = "sentinel_ui_inventory_paginator"
LOG_PATH = "/home/workspace/logs/sentinel_ui_inventory.log"
WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
QUERY_TIMEOUT = 8.0

fh = logging.FileHandler(LOG_PATH)
fh.setLevel(logging.INFO)
log = logging.getLogger(SERVICE_NAME)
log.setLevel(logging.INFO)
log.addHandler(fh)
log.propagate = False

router = APIRouter(prefix="/inventory", tags=["inventory"])


def ws_query(sql: str, params: Optional[dict] = None) -> dict:
    try:
        with httpx.Client(timeout=QUERY_TIMEOUT) as client:
            payload = {"sql": sql}
            if params:
                payload["params"] = params
            resp = client.post(WRITE_SERVICE_URL, json=payload)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException:
        log.warning("WriteService timeout on: %s", sql[:200])
        raise
    except httpx.HTTPStatusError as e:
        log.warning("WriteService HTTP error %d: %s", e.response.status_code, sql[:200])
        raise
    except Exception as e:
        log.warning("WriteService connection error: %s", str(e))
        raise


def get_total_rows(sql_where_only: str, params: dict) -> int:
    count_sql = f"SELECT COUNT(*) AS cnt FROM ({sql_where_only}) AS t"
    try:
        result = ws_query(count_sql, params)
        rows = result.get("rows", [])
        if rows and "cnt" in rows[0]:
            return int(rows[0]["cnt"])
        return 0
    except Exception:
        log.error("Failed to fetch total_rows count")
        return 0


def build_inventory_results(rows: list) -> list:
    results = []
    for row in rows:
        results.append({
            "server_id": row.get("server_id"),
            "display_name": row.get("display_name"),
            "source": row.get("registry_source"),
            "first_seen": row.get("first_seen"),
            "latest_verdict": row.get("latest_verdict"),
            "latest_overall_risk": row.get("latest_overall_risk"),
            "latest_scored_at": row.get("latest_scored_at"),
            "signal_count": row.get("signal_count", 0)
        })
    return results


@router.get("")
def list_inventory(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=10),
    verdict: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None)
) -> JSONResponse:
    if page_size > 200:
        raise HTTPException(status_code=400, detail="page_size exceeds hard cap of 200")

    log.info("GET /inventory page=%d page_size=%d verdict=%s search=%s", page, page_size, verdict, search)

    base_sql = """
    SELECT
        r.server_id,
        r.display_name,
        r.registry_source,
        r.first_seen,
        sig.latest_verdict,
        sig.latest_overall_risk,
        sig.latest_scored_at,
        sig.signal_count
    FROM mcp_server_registry r
    LEFT JOIN (
        SELECT
            server_id,
            MAX(scored_at) AS latest_scored_at,
            MAX(scored_at) FILTER (WHERE signal_name = 'latest_verdict') AS latest_verdict,
            MAX(scored_at) FILTER (WHERE signal_name = 'latest_overall_risk') AS latest_overall_risk_val,
            COUNT(*) AS signal_count,
            MAX(scored_at) AS max_scored
        FROM mcp_signal_scores
        GROUP BY server_id
    ) sig ON r.server_id = sig.server_id
    LEFT JOIN (
        SELECT server_id, signal_name, score AS signal_score, scored_at
        FROM mcp_signal_scores s1
        WHERE scored_at = (
            SELECT MAX(scored_at) FROM mcp_signal_scores s2 WHERE s2.server_id = s1.server_id AND s2.signal_name = s1.signal_name
        )
    ) latest_signals ON r.server_id = latest_signals.server_id
    """
    where_clauses = []
    params = {}

    if verdict:
        where_clauses.append("latest_signals.signal_name = 'latest_verdict' AND latest_signals.signal_score = :verdict")
        params["verdict"] = verdict

    if search:
        where_clauses.append("(r.server_id LIKE :search OR r.display_name LIKE :search)")
        params["search"] = f"%{search}%"

    if where_clauses:
        base_sql += " WHERE " + " AND ".join(where_clauses)

    total_sql = base_sql.replace("sig.latest_verdict", "sig.max_scored AS latest_verdict", 1)
    total_sql = total_sql.replace("sig.latest_overall_risk", "sig.max_scored AS latest_overall_risk", 1)

    count_sql = f"SELECT COUNT(*) AS cnt FROM ({base_sql}) AS subq"
    try:
        count_result = ws_query(count_sql, params)
        count_rows = count_result.get("rows", [])
        total_rows = int(count_rows[0]["cnt"]) if count_rows else 0
    except Exception as e:
        log.error("Count query failed: %s", str(e))
        return JSONResponse(status_code=503, content={"error": "writeservice_unavailable"})

    offset = (page - 1) * page_size
    paginated_sql = f"{base_sql} LIMIT {page_size} OFFSET {offset}"

    try:
        result = ws_query(paginated_sql, params)
        rows = result.get("rows", [])
        results = build_inventory_results(rows)
    except Exception as e:
        log.error("Paginated query failed: %s", str(e))
        return JSONResponse(status_code=503, content={"error": "writeservice_unavailable"})

    total_pages = math.ceil(total_rows / page_size) if total_rows > 0 else 0

    log.info("Returning page %d of %d (total %d rows)", page, total_pages, total_rows)

    return JSONResponse(content={
        "page": page,
        "page_size": page_size,
        "total_rows": total_rows,
        "total_pages": total_pages,
        "results": results
    })


@router.get("/{server_id}")
def get_inventory_detail(server_id: str) -> JSONResponse:
    log.info("GET /inventory/%s", server_id)

    registry_sql = """
    SELECT * FROM mcp_server_registry WHERE server_id = :server_id
    """
    try:
        reg_result = ws_query(registry_sql, {"server_id": server_id})
        reg_rows = reg_result.get("rows", [])
        if not reg_rows:
            raise HTTPException(status_code=404, detail=f"server_id not found: {server_id}")
        registry = reg_rows[0]
    except HTTPException:
        raise
    except Exception as e:
        log.error("Registry query failed for %s: %s", server_id, str(e))
        return JSONResponse(status_code=503, content={"error": "writeservice_unavailable"})

    signals_sql = """
    SELECT signal_name, score, evidence, scored_at
    FROM mcp_signal_scores
    WHERE server_id = :server_id
    AND scored_at = (
        SELECT MAX(scored_at) FROM mcp_signal_scores
        WHERE server_id = :server_id AND signal_name = mcp_signal_scores.signal_name
    )
    ORDER BY signal_name
    """
    try:
        sig_result = ws_query(signals_sql, {"server_id": server_id})
        signals = sig_result.get("rows", [])
    except Exception as e:
        log.error("Signals query failed for %s: %s", server_id, str(e))
        return JSONResponse(status_code=503, content={"error": "writeservice_unavailable"})

    fingerprint_sql = """
    SELECT * FROM mcp_fingerprints WHERE server_id = :server_id
    """
    try:
        fp_result = ws_query(fingerprint_sql, {"server_id": server_id})
        fp_rows = fp_result.get("rows", [])
        fingerprint = fp_rows[0] if fp_rows else {}
    except Exception as e:
        log.error("Fingerprint query failed for %s: %s", server_id, str(e))
        return JSONResponse(status_code=503, content={"error": "writeservice_unavailable"})

    log.info("Detail fetched for %s: %d signals, fingerprint=%s", server_id, len(signals), bool(fingerprint))

    return JSONResponse(content={
        "server_id": server_id,
        "registry": registry,
        "signals": signals,
        "fingerprint": fingerprint
    })


def run():
    import uvicorn
    app = FastAPI()
    app.include_router(router)
    uvicorn.run(app, host="127.0.0.1", port=8796)


if __name__ == "__main__":
    run()
from fastapi import FastAPI
router = APIRouter()

def run():
    import uvicorn
    app = FastAPI()
    app.include_router(router)
    uvicorn.run(app, host="127.0.0.1", port=8796)

if __name__ == "__main__":
    run()