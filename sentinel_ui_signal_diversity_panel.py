import logging
from pathlib import Path
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse

router = APIRouter()

LOG_PATH = Path("/home/workspace/logs/sentinel_ui_signal_diversity.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("sentinel_ui_signal_diversity")
logger.setLevel(logging.INFO)
_handler = logging.FileHandler(LOG_PATH)
_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(_handler)

WRITESERVICE_URL = "http://127.0.0.1:8772"
QUERY_ENDPOINT = f"{WRITESERVICE_URL}/query"
TIMEOUT = 5.0


def _call_query(sql: str, params: Optional[list] = None) -> dict:
    payload: dict = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(QUERY_ENDPOINT, json=payload)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    "WriteService 5xx", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp.json()
    except (httpx.ConnectError, httpx.RemoteProtocolError):
        logger.error("WriteService unavailable (connection error)")
        raise HTTPException(
            status_code=503,
            detail={"error": "writeservice_unavailable"},
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500:
            logger.error(f"WriteService returned {exc.response.status_code}")
            raise HTTPException(
                status_code=503,
                detail={"error": "writeservice_unavailable"},
            )
        raise


@router.get("/signal-diversity")
async def get_signal_diversity(response: Response):
    logger.info("GET /signal-diversity")
    result = _call_query(
        "SELECT signal_name, COUNT(DISTINCT score_value) AS distinct_values, "
        "COUNT(*) AS total_rows FROM mcp_signal_scores "
        "GROUP BY signal_name ORDER BY distinct_values ASC, total_rows DESC"
    )
    rows = result.get("rows", [])
    signals = []
    for row in rows:
        signal_name = row.get("signal_name", "")
        distinct_values = row.get("distinct_values", 0)
        total_rows = row.get("total_rows", 0)
        diversity_ratio = distinct_values / max(total_rows, 1)
        signals.append({
            "signal_name": signal_name,
            "distinct_values": distinct_values,
            "total_rows": total_rows,
            "diversity_ratio": round(diversity_ratio, 6),
        })
    logger.info(f"Returning {len(signals)} signals")
    response.headers["Cache-Control"] = "public, max-age=60"
    return JSONResponse(content={"signals": signals})


@router.get("/signal-diversity/{signal_name}")
async def get_signal_distribution(
    signal_name: str, response: Response
):
    logger.info(f"GET /signal-diversity/{signal_name}")
    result = _call_query(
        "SELECT score_value, COUNT(*) AS n FROM mcp_signal_scores "
        "WHERE signal_name = ? GROUP BY score_value ORDER BY n DESC LIMIT 50",
        params=[signal_name],
    )
    rows = result.get("rows", [])
    distribution = [
        {"score_value": row.get("score_value"), "n": row.get("n", 0)}
        for row in rows
    ]
    logger.info(
        f"Returning {len(distribution)} distinct score values for '{signal_name}'"
    )
    response.headers["Cache-Control"] = "public, max-age=60"
    return JSONResponse(content={
        "signal_name": signal_name,
        "distribution": distribution,
    })