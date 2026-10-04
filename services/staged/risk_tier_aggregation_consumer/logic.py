# services/staged/risk_tier_aggregation_consumer/logic.py
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Dict, Set
from sqlalchemy.orm import Session

# Real application imports
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class TierInfo(BaseModel):
    tier: str
    count: int
    avg_confidence: float
    axis_coverage: int
    servers: List[int]


class AggregationResponse(BaseModel):
    tiers: List[TierInfo]


@router.get(
    "/risk/aggregation",
    response_model=AggregationResponse,
    status_code=status.HTTP_200_OK,
    summary="Aggregate risk tier statistics",
)
def get_risk_tier_aggregation(session: Session = Depends(get_session)):
    """
    Reads all rows from `mcp_llm_axis_scores` joined to `mcp_server_registry`,
    groups by `risk_tier` from `mcp_server_registry` and computes per‑tier
    statistics:
      * count of servers
      * average confidence (from `mcp_server_registry.confidence`)
      * axis coverage (distinct `axis_name` values across the tier)
      * list of server IDs belonging to the tier
    """
    # Load all servers and scores in a single round‑trip each
    servers: List[McpServerRegistry] = session.query(McpServerRegistry).all()
    scores: List[McpLlmAxisScore] = session.query(McpLlmAxisScore).all()

    # Index scores by server_id for fast lookup
    scores_by_server: Dict[int, List[McpLlmAxisScore]] = {}
    for s in scores:
        scores_by_server.setdefault(s.server_id, []).append(s)

    tier_data: Dict[str, Dict] = {}
    for srv in servers:
        tier = srv.risk_tier
        if tier is None:
            continue

        if tier not in tier_data:
            tier_data[tier] = {
                "count": 0,
                "conf_sum": 0.0,
                "servers": set(),
                "axes": set(),
            }

        tier_entry = tier_data[tier]
        tier_entry["count"] += 1
        tier_entry["conf_sum"] += float(srv.confidence or 0.0)
        tier_entry["servers"].add(srv.server_id)

        for sc in scores_by_server.get(srv.server_id, []):
            tier_entry["axes"].add(sc.axis_name)

    result: List[TierInfo] = []
    for tier, data in tier_data.items():
        count = data["count"]
        avg_conf = data["conf_sum"] / count if count else 0.0
        result.append(
            TierInfo(
                tier=tier,
                count=count,
                avg_confidence=round(avg_conf, 4),
                axis_coverage=len(data["axes"]),
                servers=sorted(data["servers"]),
            )
        )

    # Sort tiers alphabetically for deterministic output
    result.sort(key=lambda x: x.tier)

    return AggregationResponse(tiers=result)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ----------------------------------------------------------------------- #
    # Mock objects mimicking the real ORM models
    # ----------------------------------------------------------------------- #
    class MockServer:
        def __init__(self, server_id: int, risk_tier: str, confidence: float):
            self.server_id = server_id
            self.risk_tier = risk_tier
            self.confidence = confidence

    class MockScore:
        def __init__(self, server_id: int, axis_name: str):
            self.server_id = server_id
            self.axis_name = axis_name

    # ----------------------------------------------------------------------- #
    # Mock session that returns predefined data
    # ----------------------------------------------------------------------- #
    class MockQuery:
        def __init__(self, data):
            self._data = data

        def all(self):
            return self._data

    class MockSession:
        def __init__(self):
            self._servers = [
                MockServer(server_id=1, risk_tier="high", confidence=0.9),
                MockServer(server_id=2, risk_tier="medium", confidence=0.7),
                MockServer(server_id=3, risk_tier="high", confidence=0.8),
            ]
            self._scores = [
                MockScore(server_id=1, axis_name="security"),
                MockScore(server_id=1, axis_name="performance"),
                MockScore(server_id=2, axis_name="security"),
                MockScore(server_id=3, axis_name="reliability"),
                MockScore(server_id=3, axis_name="security"),
            ]

        def query(self, model):
            if model is McpServerRegistry:
                return MockQuery(self._servers)
            if model is McpLlmAxisScore:
                return MockQuery(self._scores)
            raise ValueError("Unexpected model queried")

    # ----------------------------------------------------------------------- #
    # Build FastAPI app with dependency override
    # ----------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    def get_mock_session():
        return MockSession()

    app.dependency_overrides[get_session] = get_mock_session

    client = TestClient(app)

    resp = client.get("/api/risk/aggregation")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert "tiers" in payload, "Missing 'tiers' key"
    tiers = {t["tier"]: t for t in payload["tiers"]}

    # Expected tiers
    for expected in ("high", "medium"):
        assert expected in tiers, f"Tier {expected} missing"

    # Basic sanity checks
    high = tiers["high"]
    assert high["count"] == 2
    assert high["servers"] == [1, 3]
    assert high["axis_coverage"] == 3  # security, performance, reliability
    assert round(high["avg_confidence"], 2) == 0.85

    medium = tiers["medium"]
    assert medium["count"] == 1
    assert medium["servers"] == [2]
    assert medium["axis_coverage"] == 1  # security
    assert round(medium["avg_confidence"], 2) == 0.70

    print("PASS")