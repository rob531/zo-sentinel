# services/staged/server_scorecard/logic.py
from __future__ import annotations

import json
from typing import Any, Dict, List

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class ScoreDetail(BaseModel):
    confidence: float
    evidence_blob: Dict[str, Any] | None = None


class ScorecardResponse(BaseModel):
    scores: Dict[str, ScoreDetail]
    overall_score: float
    risk_tier: str


# --------------------------------------------------------------------------- #
# Helper – bus query
# --------------------------------------------------------------------------- #
BUS_URL = "http://127.0.0.1:8772/query"


def _bus_query(table: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Minimal wrapper around the write‑service bus query endpoint.
    """
    payload = {"table": table, "filters": filters}
    try:
        resp = requests.post(BUS_URL, json=payload, timeout=5)
        resp.raise_for_status()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Bus query failed: {exc}") from exc
    try:
        data = resp.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=502, detail="Invalid JSON from bus") from exc
    # The bus returns {"rows": [...]} in the current schema
    return data.get("rows", [])


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #
def _fetch_scores(server_id: int) -> List[Dict[str, Any]]:
    return _bus_query("mcp_signal_scores", {"server_id": server_id})


def _fetch_enrichments(server_id: int) -> List[Dict[str, Any]]:
    return _bus_query("mcp_signal_enrichments", {"server_id": server_id})


def _assemble_scorecard(
    raw_scores: List[Dict[str, Any]], raw_enrich: List[Dict[str, Any]]
) -> ScorecardResponse:
    # Index enrichments by signal_type for quick lookup
    enrich_map: Dict[str, Dict[str, Any]] = {
        e["signal_type"]: e for e in raw_enrich if "signal_type" in e
    }

    scores: Dict[str, ScoreDetail] = {}
    confidence_vals: List[float] = []

    for s in raw_scores:
        signal_type = s.get("signal_type")
        if not signal_type:
            continue
        confidence = float(s.get("confidence", 0.0))
        confidence_vals.append(confidence)
        enrichment = enrich_map.get(signal_type, {})
        evidence_blob = enrichment.get("evidence_blob")
        scores[signal_type] = ScoreDetail(
            confidence=confidence, evidence_blob=evidence_blob
        )

    overall_score = (
        sum(confidence_vals) / len(confidence_vals) if confidence_vals else 0.0
    )
    if overall_score < 0.3:
        tier = "low"
    elif overall_score < 0.7:
        tier = "medium"
    else:
        tier = "high"

    return ScorecardResponse(
        scores=scores, overall_score=overall_score, risk_tier=tier
    )


def get_server_scorecard(server_id: int, session=Depends(get_session)) -> ScorecardResponse:
    """
    Public helper used by the router and by other services.
    """
    # Verify the server exists – the authoritative table lives in the app DB.
    server = (
        session.query(McpServerRegistry)
        .filter_by(server_id=server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    raw_scores = _fetch_scores(server_id)
    raw_enrich = _fetch_enrichments(server_id)
    return _assemble_scorecard(raw_scores, raw_enrich)


# --------------------------------------------------------------------------- #
# Router
# --------------------------------------------------------------------------- #
@router.get(
    "/servers/{server_id}/scorecard",
    response_model=ScorecardResponse,
    tags=["server_scorecard"],
)
def read_scorecard(server_id: int, session=Depends(get_session)):
    return get_server_scorecard(server_id, session)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Minimal FastAPI app for the self‑test
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------- #
    # Dependency override – fake DB session
    # ------------------------------------------------------------------- #
    class _FakeQuery:
        def __init__(self, model):
            self._model = model
            self._filters: Dict[str, Any] = {}

        def filter_by(self, **kw):
            self._filters.update(kw)
            return self

        def first(self):
            # Only McpServerRegistry is queried; we accept any server_id > 0
            if self._model is McpServerRegistry and self._filters.get("server_id"):
                return type("Obj", (), {"server_id": self._filters["server_id"]})
            return None

    class _FakeSession:
        def query(self, model):
            return _FakeQuery(model)

    def _override_get_session():
        return _FakeSession()

    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------- #
    # Monkey‑patch requests.post to return deterministic data
    # ------------------------------------------------------------------- #
    def _fake_post(url, json, timeout):
        table = json.get("table")
        filters = json.get("filters", {})
        server_id = filters.get("server_id")
        if table == "mcp_signal_scores":
            rows = [
                {"signal_type": "malware", "confidence": 0.9},
                {"signal_type": "phishing", "confidence": 0.4},
            ]
        elif table == "mcp_signal_enrichments":
            rows = [
                {
                    "signal_type": "malware",
                    "evidence_blob": {"detail": "known malware hash"},
                },
                {
                    "signal_type": "phishing",
                    "evidence_blob": {"detail": "spear‑phish campaign"},
                },
            ]
        else:
            rows = []

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"rows": rows}

        return _Resp()

    requests.post = _fake_post  # type: ignore

    # ------------------------------------------------------------------- #
    # Execute test
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/servers/123/scorecard")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    # Validate presence of a known signal
    assert "malware" in data["scores"], "malware score missing"
    print("PASS")