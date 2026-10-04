import sys
import logging
import logging.handlers
from datetime import datetime, timezone
from typing import Optional
import json
import httpx

from fastapi import FastAPI, APIRouter, HTTPException, Path, Response
from fastapi.responses import JSONResponse

SERVICE_NAME = "sentinel_ui_evidence_drawer"
PORT = 8788
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
LOG_PATH = "/home/workspace/logs/sentinel_ui_evidence.log"

router = APIRouter(prefix="/evidence", tags=["evidence"])

_file_handler = logging.handlers.RotatingFileHandler(
    LOG_PATH, maxBytes=5_000_000, backupCount=3
)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s")
)
_logger = logging.getLogger(SERVICE_NAME)
_logger.addHandler(_file_handler)
_logger.setLevel(logging.INFO)
del _file_handler  # only keep the reference inside logger


def _ws_query(sql: str, params: Optional[list] = None) -> dict:
    try:
        with httpx.Client(timeout=5.0) as client:
            payload: dict = {"sql": sql}
            if params is not None:
                payload["params"] = params
            resp = client.post(WRITE_SERVICE_URL + "/query", json=payload)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        _logger.error("WriteService unavailable: %s", exc)
        raise HTTPException(status_code=503, detail={"error": "writeservice_unavailable"})


@router.get("/{server_id}/{signal_name}")
def get_signal_evidence(
    server_id: str = Path(..., description="MCP server ID"),
    signal_name: str = Path(..., description="Signal name, e.g. source_diversity"),
    response: Response = None,
):
    _logger.info(
        "GET /evidence/%s/%s", server_id, signal_name
    )
    sql = (
        "SELECT signal_name, score_value, evidence, scored_at "
        "FROM mcp_signal_scores "
        "WHERE server_id = ? AND signal_name = ? "
        "ORDER BY scored_at DESC LIMIT 1"
    )
    result = _ws_query(sql, params=[server_id, signal_name])
    rows = result.get("rows", [])
    if not rows:
        _logger.info("No evidence found for server_id=%s signal_name=%s", server_id, signal_name)
        raise HTTPException(
            status_code=404,
            detail={"error": "no_evidence"},
        )
    row = rows[0]
    raw_evidence = row.get("evidence", "{}")
    try:
        parsed_evidence = (
            json.loads(raw_evidence)
            if isinstance(raw_evidence, str)
            else raw_evidence
        )
    except Exception:
        raw_str = str(raw_evidence)
        _logger.warning("evidence parse failed for server_id=%s signal_name=%s", server_id, signal_name)
        raise HTTPException(
            status_code=200,
            detail={
                "server_id": server_id,
                "signal_name": signal_name,
                "score_value": row.get("score_value"),
                "evidence": {"error": "evidence_parse_failed", "raw": raw_str[:500]},
                "scored_at": row.get("scored_at"),
            },
        )
    body = {
        "server_id": server_id,
        "signal_name": signal_name,
        "score_value": row.get("score_value"),
        "evidence": parsed_evidence,
        "scored_at": row.get("scored_at"),
    }
    if response:
        response.headers["Cache-Control"] = "public, max-age=120"
    _logger.info(
        "Returning evidence for server_id=%s signal_name=%s score=%s",
        server_id,
        signal_name,
        row.get("score_value"),
    )
    return body


@router.get("/{server_id}")
def get_all_signals_evidence(
    server_id: str = Path(..., description="MCP server ID"),
    response: Response = None,
):
    _logger.info("GET /evidence/%s (all signals)", server_id)
    sql = (
        "SELECT signal_name, score_value, evidence, scored_at "
        "FROM mcp_signal_scores "
        "WHERE server_id = ? "
        "ORDER BY signal_name ASC, scored_at DESC"
    )
    result = _ws_query(sql, params=[server_id])
    rows = result.get("rows", [])
    signals = []
    for row in rows:
        raw_evidence = row.get("evidence", "{}")
        try:
            parsed = (
                json.loads(raw_evidence)
                if isinstance(raw_evidence, str)
                else raw_evidence
            )
        except Exception:
            raw_str = str(raw_evidence)
            parsed = {"error": "evidence_parse_failed", "raw": raw_str[:500]}
            _logger.warning(
                "evidence parse failed for server_id=%s signal=%s",
                server_id,
                row.get("signal_name"),
            )
        signals.append(
            {
                "signal_name": row.get("signal_name"),
                "score_value": row.get("score_value"),
                "evidence": parsed,
                "scored_at": row.get("scored_at"),
            }
        )
    body = {"server_id": server_id, "signals": signals}
    if response:
        response.headers["Cache-Control"] = "public, max-age=120"
    _logger.info(
        "Returning %d signals for server_id=%s",
        len(signals),
        server_id,
    )
    return body


app = FastAPI(title="sentinel_ui_evidence_drawer")
app.include_router(router)


def run():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    run()
    sys.exit(0)
else:
    sys.exit(0)