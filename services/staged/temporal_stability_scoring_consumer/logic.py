import threading
import time
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def _query_signal_enrichments(
    server_ids: Optional[List[str]] = None,
) -> List[dict]:
    """Query the mesh write‑service for temporal_stability enrichments."""
    where: dict = {"signal_type": "temporal_stability"}
    if server_ids:
        where["server_id"] = {"$in": server_ids}
    payload = {
        "select": ["server_id", "p_top"],
        "from": "mcp_signal_enrichments",
        "where": where,
    }
    resp = requests.post(f"{WRITE_SERVICE_URL}/query", json=payload, timeout=10)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="write‑service query failed")
    return resp.json().get("rows", [])


def _upsert_axis_scores(records: List[dict]) -> None:
    """Upsert axis scores into the mesh write‑service."""
    if not records:
        return
    payload = {
        "table": "mcp_llm_axis_scores",
        "records": records,
        "conflict_target": ["server_id", "axis_name"],
        "update_columns": ["p_top"],
    }
    resp = requests.post(f"{WRITE_SERVICE_URL}/write", json=payload, timeout=10)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="write‑service write failed")


@router.post("/consume/temporal-stability")
def consume_temporal_stability(
    payload: dict,
    session: Session = Depends(get_session),
) -> dict:
    """
    Consume temporal stability signals and upsert them as axis scores.

    Expected payload:
        {"server_ids": ["srv1", "srv2", ...]}   # empty list → all servers
    """
    server_ids = payload.get("server_ids", [])
    if not isinstance(server_ids, list):
        raise HTTPException(status_code=422, detail="server_ids must be a list")
    enrichments = _query_signal_enrichments(server_ids)

    # Filter out rows without a p_top value
    records = [
        {
            "server_id": row["server_id"],
            "axis_name": "temporal_stability",
            "p_top": row["p_top"],
        }
        for row in enrichments
        if row.get("p_top") is not None
    ]

    _upsert_axis_scores(records)
    return {"processed": len(records)}


# --------------------------------------------------------------------------- #
# Heartbeat handling (runs every 60 seconds)
# --------------------------------------------------------------------------- #
def _send_heartbeat() -> None:
    payload = {"service_name": "temporal_stability_scoring_consumer", "timestamp": int(time.time())}
    try:
        requests.post(f"{WRITE_SERVICE_URL}/service_health", json=payload, timeout=5)
    except Exception:
        # Heartbeat failures are non‑fatal; they are logged elsewhere.
        pass
    finally:
        # Schedule next heartbeat
        threading.Timer(60.0, _send_heartbeat).start()


# Start the periodic heartbeat when the module is imported
_threading_heartbeat = threading.Thread(target=_send_heartbeat, daemon=True)
_threading_heartbeat.start()


# --------------------------------------------------------------------------- #
# Self‑test --------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # ------------------------------------------------------------------- #
    # Minimal in‑process mock of the write‑service
    # ------------------------------------------------------------------- #
    from fastapi.testclient import TestClient

    class _SimpleResponse:
        def __init__(self, data: dict, status_code: int = 200):
            self._data = data
            self.status_code = status_code

        def json(self) -> dict:
            return self._data

    class _MockWriteService:
        storage = {
            "mcp_signal_enrichments": [
                {"server_id": "srv-1", "signal_type": "temporal_stability", "p_top": 0.82},
                {"server_id": "srv-2", "signal_type": "temporal_stability", "p_top": None},
            ],
            "mcp_llm_axis_scores": [],
            "mcp_server_registry": [
                {
                    "server_id": "srv-1",
                    "name": "test‑server",
                    "url": "https://example.com",
                    "risk_tier": "low",
                    "trust_score": 0.9,
                    "verdict": "clean",
                }
            ],
        }

        @staticmethod
        def post(url: str, json: dict, timeout: int = 10) -> _SimpleResponse:
            if url.endswith("/query"):
                table = json.get("from")
                select = json.get("select", [])
                where = json.get("where", {})
                rows = []
                for row in _MockWriteService.storage.get(table, []):
                    match = True
                    for k, v in where.items():
                        if isinstance(v, dict) and "$in" in v:
                            if row.get(k) not in v["$in"]:
                                match = False
                                break
                        else:
                            if row.get(k) != v:
                                match = False
                                break
                    if match:
                        rows.append({col: row.get(col) for col in select})
                return _SimpleResponse({"rows": rows})
            elif url.endswith("/write"):
                table = json["table"]
                records = json["records"]
                conflict = json.get("conflict_target", [])
                update_cols = json.get("update_columns", [])
                tbl = _MockWriteService.storage.setdefault(table, [])
                for rec in records:
                    existing = None
                    for r in tbl:
                        if all(r.get(col) == rec.get(col) for col in conflict):
                            existing = r
                            break
                    if existing:
                        for col in update_cols:
                            existing[col] = rec[col]
                    else:
                        tbl.append(rec)
                return _SimpleResponse({"status": "ok"})
            elif url.endswith("/service_health"):
                # ignore heartbeat in test
                return _SimpleResponse({"status": "ok"})
            else:
                return _SimpleResponse({"error": "unknown endpoint"}, status_code=404)

    # Monkey‑patch requests.post for the duration of the test
    _original_post = requests.post
    requests.post = _MockWriteService.post

    # ------------------------------------------------------------------- #
    # Build a minimal FastAPI app that includes the router
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override the DB session dependency with a dummy (not used in this test)
    def _dummy_session():
        return None

    app.dependency_overrides[get_session] = _dummy_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute the consumer with an empty server list (process all)
    # ------------------------------------------------------------------- #
    response = client.post(
        "/consume/temporal-stability", json={"server_ids": []}
    )
    assert response.status_code == 200, f"unexpected status {response.status_code}"
    assert response.json()["processed"] == 1, "should have processed exactly one row"

    # Verify that the upserted axis score is present and correct
    scores = _MockWriteService.storage["mcp_llm_axis_scores"]
    assert (
        len(scores) == 1
    ), f"expected 1 axis score row, found {len(scores)}"
    score = scores[0]
    assert (
        score["axis_name"] == "temporal_stability"
    ), "axis_name mismatch"
    assert (
        abs(score["p_top"] - 0.82) < 1e-9
    ), f"p_top value mismatch: {score['p_top']}"

    # Restore original requests.post
    requests.post = _original_post

    print("PASS")