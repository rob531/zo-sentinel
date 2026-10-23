# services/staged/risk_tier_writer/logic.py
from __future__ import annotations

import asyncio
from typing import List, Dict

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class WriteRequest(BaseModel):
    dry_run: bool = True


def _risk_tier_from_scores(scores: List[McpLlmAxisScore]) -> str:
    """
    Derive a risk tier from a list of axis scores.
    Mirrors the rule‑set used elsewhere in the codebase:
        - if any p_top >= 0.9 → "CRITICAL"
        - elif any p_top >= 0.7 → "DANGER"
        - else → "NOMINAL"
    """
    for s in scores:
        if getattr(s, "p_top", None) is not None and s.p_top >= 0.9:
            return "CRITICAL"
    for s in scores:
        if getattr(s, "p_top", None) is not None and s.p_top >= 0.7:
            return "DANGER"
    return "NOMINAL"


@router.post("/internal/scoring/risk-tier-write")
async def write_risk_tiers(
    payload: WriteRequest,
    session=Depends(get_session),
) -> Dict:
    # Gather distinct server ids from the axis scores table
    axis_scores: List[McpLlmAxisScore] = (
        session.query(McpLlmAxisScore).all()
    )
    if not axis_scores:
        raise HTTPException(status_code=404, detail="No axis scores found")

    # Organise scores per server
    scores_by_server: Dict[int, List[McpLlmAxisScore]] = {}
    for sc in axis_scores:
        scores_by_server.setdefault(sc.server_id, []).append(sc)

    # Compute proposed tiers
    proposals = [
        {"server_id": sid, "proposed_tier": _risk_tier_from_scores(slist)}
        for sid, slist in scores_by_server.items()
    ]

    if payload.dry_run:
        return {"servers": proposals}

    # Prepare rows for write‑service
    rows = [
        {"server_id": p["server_id"], "risk_tier": p["proposed_tier"]} for p in proposals
    ]

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://127.0.0.1:8772/write",
            json={
                "table": "mcp_server_registry",
                "rows": rows,
                "on_conflict": "DO UPDATE",
            },
        )
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Write service error: {resp.status_code} {resp.text}",
        )
    return {"updated": len(rows)}


# --------------------------------------------------------------------------- #
# Self‑test ----------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # --------------------------------------------------------------------- #
    # Mock data layer ------------------------------------------------------ #
    # --------------------------------------------------------------------- #
    class MockSession:
        def __init__(self, scores: List[McpLlmAxisScore]):
            self._scores = scores

        def query(self, model):
            class Q:
                def __init__(self, data):
                    self._data = data

                def all(self):
                    return self._data

            if model is McpLlmAxisScore:
                return Q(self._scores)
            raise NotImplementedError

    # --------------------------------------------------------------------- #
    # Mock write‑service ---------------------------------------------------- #
    # --------------------------------------------------------------------- #
    captured_rows: List[Dict] = []

    original_post = httpx.AsyncClient.post

    async def mock_post(self, url, json=None, **kwargs):
        if url.startswith("http://127.0.0.1:8772/write"):
            captured_rows.extend(json.get("rows", []))
            class Resp:
                status_code = 200
                text = "OK"

                async def aclose(self):
                    return None

            return Resp()
        return await original_post(self, url, json=json, **kwargs)

    httpx.AsyncClient.post = mock_post  # type: ignore

    # --------------------------------------------------------------------- #
    # Build mock axis scores ----------------------------------------------- #
    # --------------------------------------------------------------------- #
    mock_scores = [
        McpLlmAxisScore(
            server_id=1,
            p_top=0.95,
            adapter_sha256="a",
            axis_name="x",
            decision_rule_version="v",
            escalated=False,
            escalated_to=None,
            id=1,
            label="lbl",
            label_index=0,
            model_version="m",
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            scored_at=None,
        ),
        McpLlmAxisScore(
            server_id=2,
            p_top=0.80,
            adapter_sha256="b",
            axis_name="y",
            decision_rule_version="v",
            escalated=False,
            escalated_to=None,
            id=2,
            label="lbl",
            label_index=0,
            model_version="m",
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            scored_at=None,
        ),
        McpLlmAxisScore(
            server_id=3,
            p_top=0.50,
            adapter_sha256="c",
            axis_name="z",
            decision_rule_version="v",
            escalated=False,
            escalated_to=None,
            id=3,
            label="lbl",
            label_index=0,
            model_version="m",
            p_critical=0.0,
            p_danger=0.0,
            probs={},
            scored_at=None,
        ),
    ]

    # --------------------------------------------------------------------- #
    # Assemble FastAPI app ------------------------------------------------- #
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # Override the DB dependency with our mock session
    def get_mock_session():
        return MockSession(mock_scores)

    app.dependency_overrides[get_session] = get_mock_session

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Execute the endpoint ------------------------------------------------- #
    # --------------------------------------------------------------------- #
    response = client.post(
        "/internal/scoring/risk-tier-write", json={"dry_run": False}
    )
    assert response.status_code == 200, f"Bad status: {response.status_code}"
    data = response.json()
    assert data.get("updated") == 3, f"Unexpected updated count: {data}"

    # Verify written rows
    expected = {
        1: "CRITICAL",
        2: "DANGER",
        3: "NOMINAL",
    }
    for row in captured_rows:
        sid = row["server_id"]
        tier = row["risk_tier"]
        assert expected[sid] == tier, f"Server {sid} tier mismatch: {tier} != {expected[sid]}"

    print("PASS")

    # Cleanup monkey‑patch
    httpx.AsyncClient.post = original_post  # type: ignore