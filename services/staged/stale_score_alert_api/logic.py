from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class Alert(BaseModel):
    server_id: str
    name: str
    last_scored_at: datetime
    hours_ago: float
    risk_tier: Optional[str] = None


class StaleScoreAlertResponse(BaseModel):
    threshold_hours: int
    alerts: List[Alert]


def _compute_stale_alerts(
    threshold_hours: int, db: Session
) -> StaleScoreAlertResponse:
    now = datetime.utcnow()
    subq = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    stmt = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            subq.c.last_scored_at,
        )
        .join(subq, McpServerRegistry.server_id == subq.c.server_id)
    )

    alerts: List[Alert] = []
    for row in db.execute(stmt):
        last_scored_at: datetime = row.last_scored_at
        hours_ago = (now - last_scored_at).total_seconds() / 3600.0
        if hours_ago > threshold_hours:
            alerts.append(
                Alert(
                    server_id=row.server_id,
                    name=row.name,
                    last_scored_at=last_scored_at,
                    hours_ago=round(hours_ago, 2),
                    risk_tier=row.risk_tier,
                )
            )
    return StaleScoreAlertResponse(threshold_hours=threshold_hours, alerts=alerts)


@router.get(
    "/api/scoring/stale-alerts",
    response_model=StaleScoreAlertResponse,
    name="stale_score_alerts",
)
def stale_score_alerts(
    threshold_hours: int = 72, db: Session = Depends(get_session)
) -> StaleScoreAlertResponse:
    return _compute_stale_alerts(threshold_hours, db)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite for the self‑test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Dependency override
    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.dependency_overrides[get_session] = get_test_session
    app.include_router(router)

    # Seed data
    now = datetime.utcnow()
    old_time = now - timedelta(hours=100)
    fresh_time = now - timedelta(hours=10)

    with TestSessionLocal() as db:
        # Servers
        s1 = McpServerRegistry(
            server_id="s1", name="Server One", risk_tier="high"
        )
        s2 = McpServerRegistry(
            server_id="s2", name="Server Two", risk_tier="medium"
        )
        s3 = McpServerRegistry(
            server_id="s3", name="Server Three", risk_tier="low"
        )
        db.add_all([s1, s2, s3])

        # Scores
        score1 = McpLlmAxisScore(
            server_id="s1",
            scored_at=old_time,
            axis_name="axis",
            adapter_sha256="a",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=1,
            label="label",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.3,
            probs="[]",
        )
        score2 = McpLlmAxisScore(
            server_id="s2",
            scored_at=old_time,
            axis_name="axis",
            adapter_sha256="b",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=2,
            label="label",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.3,
            probs="[]",
        )
        score3 = McpLlmAxisScore(
            server_id="s3",
            scored_at=fresh_time,
            axis_name="axis",
            adapter_sha256="c",
            decision_rule_version="v1",
            escalated=False,
            escalated_to=None,
            id=3,
            label="label",
            label_index=0,
            model_version="m1",
            p_critical=0.1,
            p_danger=0.2,
            p_top=0.3,
            probs="[]",
        )
        db.add_all([score1, score2, score3])
        db.commit()

    client = TestClient(app)
    response = client.get("/api/scoring/stale-alerts?threshold_hours=72")
    if response.status_code == 200 and len(response.json()["alerts"]) == 2:
        print("PASS")
    else:
        print("FAIL", response.status_code, response.text)