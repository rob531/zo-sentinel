# services/staged/risk_tier_history_trend/contract.py
from datetime import date, datetime, timedelta
from typing import List

from fastapi import Depends, FastAPI, APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class RiskTierHistoryItem(BaseModel):
    date: date
    tier: int
    count: int


def _compute_history(db: Session) -> List[RiskTierHistoryItem]:
    cutoff = datetime.utcnow() - timedelta(days=30)

    stmt = (
        select(
            func.date(McpLlmAxisScore.scored_at).label("date"),
            McpServerRegistry.risk_tier.label("tier"),
            func.count(McpServerRegistry.server_id).label("count"),
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
        .group_by("date", "tier")
        .order_by("date")
    )

    results = db.execute(stmt).all()
    return [
        RiskTierHistoryItem(date=row.date, tier=row.tier, count=row.count) for row in results
    ]


@router.get("/risk/history", response_model=List[RiskTierHistoryItem])
def get_risk_tier_history(db: Session = Depends(get_session)):
    return _compute_history(db)


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    # ---- self‑test ---------------------------------------------------------
    from fastapi.testclient import TestClient

    # in‑memory SQLite engine (StaticPool) for the test
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)

    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    async def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # seed data
    with TestSessionLocal() as db:
        # servers
        srv1 = McpServerRegistry(
            server_id=1,
            name="srv1",
            risk_tier=1,
            confidence=0.0,
            description="",
            first_seen=datetime.utcnow(),
            last_assessed=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            meta="{}",
            registry_source="test",
            scan_count=0,
            trust_score=0.0,
            url="",
            verdict="",
            verdict_reasoning="",
        )
        srv2 = McpServerRegistry(
            server_id=2,
            name="srv2",
            risk_tier=1,
            confidence=0.0,
            description="",
            first_seen=datetime.utcnow(),
            last_assessed=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            meta="{}",
            registry_source="test",
            scan_count=0,
            trust_score=0.0,
            url="",
            verdict="",
            verdict_reasoning="",
        )
        srv3 = McpServerRegistry(
            server_id=3,
            name="srv3",
            risk_tier=2,
            confidence=0.0,
            description="",
            first_seen=datetime.utcnow(),
            last_assessed=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            meta="{}",
            registry_source="test",
            scan_count=0,
            trust_score=0.0,
            url="",
            verdict="",
            verdict_reasoning="",
        )
        db.add_all([srv1, srv2, srv3])
        db.flush()

        # axis scores (two days)
        day1 = datetime.utcnow() - timedelta(days=1)
        day2 = datetime.utcnow()

        scores = [
            McpLlmAxisScore(
                id=1,
                server_id=1,
                scored_at=day1,
                adapter_sha256="a",
                axis_name="x",
                decision_rule_version="v",
                escalated=False,
                escalated_to=None,
                label="",
                label_index=0,
                model_version="",
                p_critical=0.0,
                p_danger=0.0,
                p_top=0.0,
                probs="",
            ),
            McpLlmAxisScore(
                id=2,
                server_id=2,
                scored_at=day1,
                adapter_sha256="b",
                axis_name="x",
                decision_rule_version="v",
                escalated=False,
                escalated_to=None,
                label="",
                label_index=0,
                model_version="",
                p_critical=0.0,
                p_danger=0.0,
                p_top=0.0,
                probs="",
            ),
            McpLlmAxisScore(
                id=3,
                server_id=3,
                scored_at=day2,
                adapter_sha256="c",
                axis_name="x",
                decision_rule_version="v",
                escalated=False,
                escalated_to=None,
                label="",
                label_index=0,
                model_version="",
                p_critical=0.0,
                p_danger=0.0,
                p_top=0.0,
                probs="",
            ),
        ]
        db.add_all(scores)
        db.commit()

    client = TestClient(app)

    resp = client.get("/api/risk/history")
    assert resp.status_code == 200, f"unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, list), "response not a list"
    assert len(data) == 2, f"expected 2 entries, got {len(data)}"
    # first day should have count 2 (tier 1)
    assert data[0]["count"] == 2, f"expected count 2, got {data[0]['count']}"
    print("PASS")