from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry"])


class VerdictStalenessGroup(BaseModel):
    verdict: str
    risk_tier: str
    total: int
    stale: int
    stale_pct: float


class VerdictStalenessResponse(BaseModel):
    max_age_days: int
    generated_at: str
    groups: list[VerdictStalenessGroup]


@router.get(
    "/registry/verdict-staleness",
    response_model=VerdictStalenessResponse,
)
def get_verdict_staleness(
    max_age_days: int = Query(default=30),
    session: Session = Depends(get_session),
) -> VerdictStalenessResponse:
    max_age_days = min(max(max_age_days, 1), 3650)
    generated_at = datetime.now(timezone.utc)
    cutoff = (generated_at - timedelta(days=max_age_days)).replace(tzinfo=None)

    verdict = func.coalesce(McpServerRegistry.verdict, "unknown")
    risk_tier = func.coalesce(McpServerRegistry.risk_tier, "unknown")
    stale = case(
        (McpServerRegistry.last_assessed < cutoff, 1),
        else_=0,
    )

    statement = (
        select(
            verdict.label("verdict"),
            risk_tier.label("risk_tier"),
            func.count().label("total"),
            func.coalesce(func.sum(stale), 0).label("stale"),
        )
        .group_by(verdict, risk_tier)
        .order_by(verdict, risk_tier)
    )

    groups = []
    for row in session.execute(statement):
        total = int(row.total)
        stale_count = int(row.stale)
        groups.append(
            VerdictStalenessGroup(
                verdict=str(row.verdict),
                risk_tier=str(row.risk_tier),
                total=total,
                stale=stale_count,
                stale_pct=round((stale_count / total) * 100.0, 2) if total else 0.0,
            )
        )

    return VerdictStalenessResponse(
        max_age_days=max_age_days,
        generated_at=generated_at.isoformat(),
        groups=groups,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    test_session_factory = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeded_rows = [
        McpServerRegistry(
            server_id="stale-1",
            verdict="trusted",
            risk_tier="low",
            last_assessed=now - timedelta(days=60),
        ),
        McpServerRegistry(
            server_id="stale-2",
            verdict="review",
            risk_tier="medium",
            last_assessed=now - timedelta(days=45),
        ),
        McpServerRegistry(
            server_id="fresh-1",
            verdict="trusted",
            risk_tier="low",
            last_assessed=now - timedelta(days=5),
        ),
        McpServerRegistry(
            server_id="fresh-2",
            verdict="review",
            risk_tier="medium",
            last_assessed=now - timedelta(days=10),
        ),
        McpServerRegistry(
            server_id="fresh-3",
            verdict="blocked",
            risk_tier="high",
            last_assessed=now - timedelta(days=1),
        ),
    ]

    with test_session_factory() as test_session:
        test_session.add_all(seeded_rows)
        test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with test_session_factory() as test_session:
            yield test_session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/verdict-staleness",
            params={"max_age_days": 30},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["max_age_days"] == 30
        assert payload["groups"]
        assert sum(group["stale"] for group in payload["groups"]) == 2

    print("PASS")