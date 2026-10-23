# services/staged/score_staleness_consumer/contract.py
from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Real data layer imports (must not be stubbed)
from app.db import get_session, Base
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class ServerStaleness(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    days_since_score: int
    is_stale: bool


class StalenessResponse(BaseModel):
    servers: List[ServerStaleness]


@router.get(
    "/scoring/staleness",
    response_model=StalenessResponse,
    status_code=status.HTTP_200_OK,
    summary="Get per‑server scoring staleness",
)
def get_score_staleness(session: Session = Depends(get_session)):
    """
    Returns a list of servers with the number of days since their most recent
    LLM axis score and a flag indicating whether the score is stale (>14 days).
    """
    # Sub‑query: latest score timestamp per server
    latest_score_subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_score"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    # Join registry with latest score
    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            latest_score_subq.c.last_score,
        )
        .join(
            latest_score_subq,
            McpServerRegistry.server_id == latest_score_subq.c.server_id,
        )
    )

    rows = session.execute(stmt).all()

    servers: List[ServerStaleness] = []
    now = datetime.utcnow().replace(tzinfo=timezone.utc)

    for row in rows:
        last_score: datetime | None = row.last_score
        if last_score is not None:
            # Ensure both datetimes are timezone‑aware for subtraction
            if last_score.tzinfo is None:
                last_score = last_score.replace(tzinfo=timezone.utc)
            days = (now - last_score).days
        else:
            days = -1  # No score ever recorded

        is_stale = days > 14
        servers.append(
            ServerStaleness(
                server_id=row.server_id,
                name=row.name,
                risk_tier=row.risk_tier,
                days_since_score=days,
                is_stale=is_stale,
            )
        )

    return StalenessResponse(servers=servers)


# --------------------------------------------------------------------------- #
# Self‑test (executed via `python -m services.staged.score_staleness_consumer.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite engine and bind a session factory
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine)

    # Create tables in the in‑memory DB
    Base.metadata.create_all(test_engine)

    # Dependency override for the FastAPI app
    def get_test_session() -> Session:
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with TestSessionLocal() as db:
        # Registry entries
        servers = [
            McpServerRegistry(
                server_id="srv1",
                name="Server One",
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="srv2",
                name="Server Two",
                risk_tier="medium",
            ),
            McpServerRegistry(
                server_id="srv3",
                name="Server Three",
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="srv4",
                name="Server Four",
                risk_tier="low",
            ),
        ]
        db.add_all(servers)

        now = datetime.utcnow().replace(tzinfo=timezone.utc)

        # Scores: srv1 & srv2 stale (>14 days), srv3 & srv4 fresh (<14 days)
        scores = [
            McpLlmAxisScore(
                server_id="srv1",
                scored_at=now - timedelta(days=20),
                adapter_sha256="a1",
                axis_name="axis",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=1,
                label="label",
                label_index=0,
                model_version="1.0",
                p_critical=0.0,
                p_danger=0.0,
                p_top=0.0,
                probs="{}",
            ),
            McpLlmAxisScore(
                server_id="srv2",
                scored_at=now - timedelta(days=30),
                adapter_sha256="a2",
                axis_name="axis",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=2,
                label="label",
                label_index=0,
                model_version="1.0",
                p_critical=0.0,
                p_danger=0.0,
                p_top=0.0,
                probs="{}",
            ),
            McpLlmAxisScore(
                server_id="srv3",
                scored_at=now - timedelta(days=5),
                adapter_sha256="a3",
                axis_name="axis",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=3,
                label="label",
                label_index=0,
                model_version="1.0",
                p_critical=0.0,
                p_danger=0.0,
                p_top=0.0,
                probs="{}",
            ),
            McpLlmAxisScore(
                server_id="srv4",
                scored_at=now - timedelta(days=1),
                adapter_sha256="a4",
                axis_name="axis",
                decision_rule_version="v1",
                escalated=False,
                escalated_to=None,
                id=4,
                label="label",
                label_index=0,
                model_version="1.0",
                p_critical=0.0,
                p_danger=0.0,
                p_top=0.0,
                probs="{}",
            ),
        ]
        db.add_all(scores)
        db.commit()

    # Run test client against the endpoint
    client = TestClient(app)
    response = client.get("/api/scoring/staleness")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert "servers" in data, "Response missing 'servers' key"
    stale_count = sum(1 for s in data["servers"] if s["is_stale"])
    assert stale_count == 2, f"Expected 2 stale servers, got {stale_count}"
    print("PASS")