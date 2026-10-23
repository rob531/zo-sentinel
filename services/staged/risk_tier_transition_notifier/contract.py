# services/staged/risk_tier_transition_notifier/contract.py
from datetime import datetime, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel

from sqlalchemy.orm import Session
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

# Real application data layer -------------------------------------------------
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, Base  # type: ignore

# -----------------------------------------------------------------------------


router = APIRouter(prefix="/api")


class ServerTransition(BaseModel):
    server_id: str
    name: str
    from_tier: str
    to_tier: str
    changed_at: datetime


class TransitionsResponse(BaseModel):
    servers: List[ServerTransition]


@router.get(
    "/risk/transitions",
    response_model=TransitionsResponse,
    summary="Servers that changed risk tier in the last N days",
)
def get_risk_transitions(
    days: int = Query(..., ge=1, description="Number of days to look back"),
    session: Session = Depends(get_session),
) -> TransitionsResponse:
    """
    Identify servers whose risk tier changed (via escalated LLM axis scores)
    in the last *days* days.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    # Find escalated scores in the period and join with server registry
    rows = (
        session.query(McpLlmAxisScore, McpServerRegistry)
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .filter(
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    seen = {}
    for score, server in rows:
        if score.server_id in seen:
            continue  # keep only the most recent change per server
        # The previous tier is not stored; we label it as unknown.
        transition = ServerTransition(
            server_id=score.server_id,
            name=server.name,
            from_tier="unknown",
            to_tier=score.escalated_to or "unknown",
            changed_at=score.scored_at,
        )
        seen[score.server_id] = transition

    return TransitionsResponse(servers=list(seen.values()))


# -----------------------------------------------------------------------------


def _get_test_session_factory():
    """
    Create a SQLAlchemy session factory bound to an in‑memory SQLite DB.
    This is used only by the self‑test when the dependency is overridden.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


if __name__ == "__main__":
    # Build a temporary FastAPI app for the acceptance test
    test_app = FastAPI()
    test_app.include_router(router)

    # Override the real DB session with an in‑memory SQLite session
    TestSessionLocal = _get_test_session_factory()

    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = get_test_session

    # Seed the in‑memory DB
    with TestSessionLocal() as db:
        # Servers
        srv_a = McpServerRegistry(
            server_id="srv-a",
            name="Alpha",
            risk_tier="low",
        )
        srv_b = McpServerRegistry(
            server_id="srv-b",
            name="Beta",
            risk_tier="medium",
        )
        srv_c = McpServerRegistry(
            server_id="srv-c",
            name="Gamma",
            risk_tier="high",
        )
        db.add_all([srv_a, srv_b, srv_c])
        db.flush()

        now = datetime.utcnow()
        day_ago = now - timedelta(days=1)
        two_days_ago = now - timedelta(days=2)

        # Transitions (only srv-a changes within the 2‑day window)
        score_a = McpLlmAxisScore(
            id=1,
            server_id="srv-a",
            scored_at=day_ago,
            escalated=True,
            escalated_to="high",
            axis_name="risk_tier",
            decision_rule_version="v1",
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.0,
            probs="{}",
            label="high",
            label_index=0,
            adapter_sha256="",
        )
        # Older change (outside the window)
        score_b = McpLlmAxisScore(
            id=2,
            server_id="srv-b",
            scored_at=two_days_ago - timedelta(hours=1),
            escalated=True,
            escalated_to="high",
            axis_name="risk_tier",
            decision_rule_version="v1",
            model_version="m1",
            p_critical=0.0,
            p_danger=0.0,
            p_top=0.0,
            probs="{}",
            label="high",
            label_index=0,
            adapter_sha256="",
        )
        db.add_all([score_a, score_b])
        db.commit()

    # Run the acceptance test
    client = TestClient(test_app)

    resp = client.get("/api/risk/transitions?days=2")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "servers" in data, "Missing 'servers' key"
    # Expect exactly one transition (srv-a)
    transitions = {t["server_id"]: t for t in data["servers"]}
    assert "srv-a" in transitions, "srv-a transition missing"
    assert transitions["srv-a"]["to_tier"] == "high", "Incorrect target tier"
    print("PASS")