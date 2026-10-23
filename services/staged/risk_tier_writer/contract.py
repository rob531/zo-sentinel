# services/staged/risk_tier_writer/contract.py

from collections import defaultdict
from typing import List

import httpx
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()


class RiskTierWriteRequest(BaseModel):
    dry_run: bool = True


class RiskTierDryRunItem(BaseModel):
    server_id: str
    proposed_tier: str


class RiskTierDryRunResponse(BaseModel):
    results: List[RiskTierDryRunItem]


class RiskTierWriteResponse(BaseModel):
    updated: int


def get_write_client() -> httpx.AsyncClient:
    """Dependency that provides an async HTTP client pointing at the write‑service."""
    return httpx.AsyncClient(base_url="http://127.0.0.1:8772")


def _determine_tier(max_ptop: float) -> str:
    """Map a max p_top value to a risk tier."""
    if max_ptop >= 0.9:
        return "CRITICAL"
    if max_ptop >= 0.7:
        return "DANGER"
    return "NOMINAL"


@router.post(
    "/internal/scoring/risk-tier-write",
    response_model=RiskTierDryRunResponse | RiskTierWriteResponse,
)
async def write_risk_tiers(
    payload: RiskTierWriteRequest,
    session: Session = Depends(get_session),
    client: httpx.AsyncClient = Depends(get_write_client),
):
    # Gather all axis scores and group by server_id
    scores_by_server = defaultdict(list)
    for row in session.query(McpLlmAxisScore).all():
        # p_top is guaranteed to be a float per the schema
        scores_by_server[row.server_id].append(row.p_top)

    # Compute the tier for each server
    computed = []
    for server_id, ptop_vals in scores_by_server.items():
        tier = _determine_tier(max(ptop_vals))
        computed.append({"server_id": server_id, "risk_tier": tier})

    if payload.dry_run:
        return RiskTierDryRunResponse(
            results=[
                RiskTierDryRunItem(server_id=item["server_id"], proposed_tier=item["risk_tier"])
                for item in computed
            ]
        )

    # Persist the computed tiers via the write‑service
    await client.post(
        "/write",
        json={
            "table": "mcp_server_registry",
            "rows": computed,
            "on_conflict": "DO UPDATE",
        },
    )
    return RiskTierWriteResponse(updated=len(computed))


# --------------------------------------------------------------------------- #
# Self‑test (executed with `python -m services.staged.risk_tier_writer.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import asyncio

    from fastapi.testclient import TestClient

    # --------------------------------------------------------------------- #
    # Mock data layer
    # --------------------------------------------------------------------- #
    class MockMcpLlmAxisScore:
        def __init__(self, server_id: str, p_top: float):
            self.server_id = server_id
            self.p_top = p_top

    mock_scores = [
        MockMcpLlmAxisScore(server_id="srv_critical", p_top=0.95),  # CRITICAL
        MockMcpLlmAxisScore(server_id="srv_danger", p_top=0.80),    # DANGER
        MockMcpLlmAxisScore(server_id="srv_nominal", p_top=0.45),   # NOMINAL
    ]

    class MockSession:
        def query(self, _model):
            class _Q:
                @staticmethod
                def all():
                    return mock_scores

            return _Q()

    # --------------------------------------------------------------------- #
    # Mock write‑service client
    # --------------------------------------------------------------------- #
    class MockWriteClient:
        def __init__(self):
            self.captured_rows: List[dict] = []

        async def post(self, url: str, json: dict):
            # Capture the rows that would be written
            self.captured_rows = json.get("rows", [])
            class _Resp:
                status_code = 200

                async def json(self):
                    return {}

            return _Resp()

    mock_write_client = MockWriteClient()

    # --------------------------------------------------------------------- #
    # Assemble FastAPI app with overrides
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    app.dependency_overrides[get_session] = lambda: MockSession()
    app.dependency_overrides[get_write_client] = lambda: mock_write_client

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Execute the endpoint (dry_run=False) and validate results
    # --------------------------------------------------------------------- #
    resp = client.post("/internal/scoring/risk-tier-write", json={"dry_run": False})
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data.get("updated") == 3, f"Expected 3 updates, got {data.get('updated')}"

    expected_rows = [
        {"server_id": "srv_critical", "risk_tier": "CRITICAL"},
        {"server_id": "srv_danger", "risk_tier": "DANGER"},
        {"server_id": "srv_nominal", "risk_tier": "NOMINAL"},
    ]
    assert mock_write_client.captured_rows == expected_rows, "Written rows do not match expectation"

    print("PASS")