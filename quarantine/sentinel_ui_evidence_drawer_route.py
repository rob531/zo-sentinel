import logging
from fastapi import APIRouter, HTTPException, Path, Query
from typing import Optional
import requests

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evidence", tags=["evidence"])

WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
QUERY_TIMEOUT = 5.0


@router.get("/{server_id}/{signal_name}")
async def get_evidence_for_signal(
    server_id: str = Path(..., description="Target server ID"),
    signal_name: str = Path(..., description="Signal name")
):
    """Get evidence dict for a specific signal on a given server."""
    query_payload = {
        "sql": "SELECT evidence FROM signal_evidence WHERE target_server_id = ? AND signal_name = ?",
        "params": [server_id, signal_name]
    }
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=query_payload, timeout=QUERY_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if data.get("rows") and len(data["rows"]) > 0:
            return data["rows"][0]
        raise HTTPException(status_code=404, detail=f"No evidence found for signal '{signal_name}' on server '{server_id}'")
    except requests.RequestException as e:
        logger.error(f"WriteService query failed: {e}")
        raise HTTPException(status_code=503, detail="Unable to query evidence service")


@router.get("/{server_id}")
async def get_all_evidence_for_server(
    server_id: str = Path(..., description="Target server ID"),
    signal_type: Optional[str] = Query(None, description="Filter by signal type pattern")
):
    """Get all evidence dicts for a given server, optionally filtered by signal type."""
    if signal_type:
        sql = "SELECT signal_name, evidence FROM signal_evidence WHERE target_server_id = ? AND signal_name LIKE ?"
        params = [server_id, f"%{signal_type}%"]
    else:
        sql = "SELECT signal_name, evidence FROM signal_evidence WHERE target_server_id = ?"
        params = [server_id]
    
    query_payload = {"sql": sql, "params": params}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=query_payload, timeout=QUERY_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        result = [{"signal_name": r["signal_name"], "evidence": r["evidence"]} for r in rows]
        return {"server_id": server_id, "count": len(result), "signals": result}
    except requests.RequestException as e:
        logger.error(f"WriteService query failed: {e}")
        raise HTTPException(status_code=503, detail="Unable to query evidence service")


def run():
    import uvicorn
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logger.info("Starting Sentinel UI Evidence Drawer Router on port 8774")
    app = FastAPI()
    app.include_router(router)
    uvicorn.run(app, host="0.0.0.0", port=8774)


if __name__ == "__main__":
    run()