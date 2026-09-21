"""trust_gating_api contract.

Provides a FastAPI endpoint to evaluate trust gating for MCP servers.
"""

from datetime import datetime
from typing import Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# The trust gating logic lives in this module.
# It must accept (url, name, axis_scores_dict) and return a dict with at least:
#   'trusted' (bool), optional 'forced_tier' (str) and 'criteria_version' (str).
from trust_gating_override import trust_gate  # type: ignore

app = FastAPI(title="Trust Gating API")


class TrustEvaluationResponse(BaseModel):
    server_id: str
    verdict: bool
    risk_tier: Optional[str] = None
    criteria_version: Optional[str] = None
    evaluated_at: datetime


@app.get(
    "/api/trust/evaluate",
    response_model=TrustEvaluationResponse,
    tags=["trust"],
)
def evaluate_trust(
    server_id: Optional[str] = Query(None, description="MCP server identifier"),
    name: Optional[str] = Query(None, description="MCP server name"),
    url: Optional[str] = Query(None, description="MCP server URL"),
    db: Depends = Depends(get_session),
):
    """Return a trust‑gate verdict for a server.

    Either ``server_id`` **or** both ``name`` and ``url`` must be supplied.
    """
    if server_id:
        server = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == server_id)
            .first()
        )
    elif name and url:
        server = (
            db.query(McpServerRegistry)
            .filter(
                McpServerRegistry.name == name,
                McpServerRegistry.url == url,
            )
            .first()
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="Provide either server_id or both name and url.",
        )

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Gather axis scores for the server.
    axis_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server.server_id)
        .all()
    )
    axis_scores: Dict[str, Dict] = {
        row.axis_name: row.probs for row in axis_rows if row.probs is not None
    }

    # Apply the trust‑gate logic.
    tg_result = trust_gate(server.url, server.name, axis_scores)

    verdict = bool(tg_result.get("trusted", False))
    forced_tier = tg_result.get("forced_tier")
    criteria_version = tg_result.get("criteria_version")

    return TrustEvaluationResponse(
        server_id=server.server_id,
        verdict=verdict,
        risk_tier=forced_tier,
        criteria_version=criteria_version,
        evaluated_at=datetime.utcnow(),
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with ``python -m services.staged.trust_gating_api.contract``)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real DB dependency)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed minimal data for two servers
    # ------------------------------------------------------------------- #
    db = TestSessionLocal()
    now = datetime.utcnow()

    srv1 = McpServerRegistry(
        server_id="srv1",
        name="Server One",
        url="http://example.com/1",
        confidence=1.0,
        description="test server 1",
        registry_source="test",
        risk_tier="low",
        trust_score=0.9,
        verdict=True,
        verdict_reasoning="auto",
    )
    srv2 = McpServerRegistry(
        server_id="srv2",
        name="Server Two",
        url="http://example.com/2",
        confidence=1.0,
        description="test server 2",
        registry_source="test",
        risk_tier="high",
        trust_score=0.2,
        verdict=False,
        verdict_reasoning="manual",
    )
    db.add_all([srv1, srv2])

    score1 = McpLlmAxisScore(
        server_id="srv1",
        axis_name="security",
        probs={"p_critical": 0.1, "p_danger": 0.2, "p_top": 0.7},
        adapter_sha256="a" * 64,
        decision_rule_version="v1",
        escalated=False,
        escalated_to=None,
        id=1,
        label="sec",
        label_index=0,
        model_version="m1",
        p_critical=0.1,
        p_danger=0.2,
        p_top=0.7,
        scored_at=now,
    )
    score2 = McpLlmAxisScore(
        server_id="srv2",
        axis_name="security",
        probs={"p_critical": 0.9, "p_danger": 0.8, "p_top": 0.3},
        adapter_sha256="b" * 64,
        decision_rule_version="v1",
        escalated=False,
        escalated_to=None,
        id=2,
        label="sec",
        label_index=0,
        model_version="m1",
        p_critical=0.9,
        p_danger=0.8,
        p_top=0.3,
        scored_at=now,
    )
    db.add_all([score1, score2])
    db.commit()
    db.close()

    # ------------------------------------------------------------------- #
    # Run the acceptance test
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    resp1 = client.get("/api/trust/evaluate", params={"server_id": "srv1"})
    resp2 = client.get("/api/trust/evaluate", params={"server_id": "srv2"})

    if resp1.status_code == 200 and resp2.status_code == 200:
        print("PASS")
        sys.exit(0)
    else:
        print("FAIL")
        sys.exit(1)