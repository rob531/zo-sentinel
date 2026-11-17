from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
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
    groups: List[VerdictStalenessGroup]


def _threshold_cutoff(max_age_days: int) -> datetime:
    now = datetime.now(timezone.utc)
    column_type = McpServerRegistry.__table__.c.last_assessed.type
    if not getattr(column_type, "timezone", False):
        now = now.replace(tzinfo=None)
    return now - timedelta(days=max_age_days)


@router.get("/registry/verdict-staleness", response_model=VerdictStalenessResponse)
def get_registry_verdict_staleness(
    max_age_days: int = Query(default=30),
    session: Session = Depends(get_session),
) -> VerdictStalenessResponse:
    max_age_days = max(1, min(3650, max_age_days))
    cutoff = _threshold_cutoff(max_age_days)

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
        )
    ).all()

    stale_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        ).where(McpServerRegistry.last_assessed < cutoff)
    ).all()

    totals: Dict[Tuple[str, str], int] = {}
    stale_counts: Dict[Tuple[str, str], int] = {}

    for _server_id, verdict, risk_tier, _last_assessed in rows:
        key = (verdict, risk_tier)
        totals[key] = totals.get(key, 0) + 1

    for _server_id, verdict, risk_tier in stale_rows:
        key = (verdict, risk_tier)
        stale_counts[key] = stale_counts.get(key, 0) + 1

    groups = [
        VerdictStalenessGroup(
            verdict=verdict,
            risk_tier=risk_tier,
            total=total,
            stale=stale_counts.get((verdict, risk_tier), 0),
            stale_pct=round(
                stale_counts.get((verdict, risk_tier), 0) / total * 100,
                2,
            ),
        )
        for (verdict, risk_tier), total in sorted(totals.items())
    ]

    return VerdictStalenessResponse(
        max_age_days=max_age_days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        groups=groups,
    )


def main() -> None:
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
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    seeded_rows = [
        McpServerRegistry(
            server_id="stale-malicious-critical",
            verdict="malicious",
            risk_tier="critical",
            last_assessed=now - timedelta(days=40),
        ),
        McpServerRegistry(
            server_id="stale-safe-low",
            verdict="safe",
            risk_tier="low",
            last_assessed=now - timedelta(days=31),
        ),
        McpServerRegistry(
            server_id="fresh-malicious-critical",
            verdict="malicious",
            risk_tier="critical",
            last_assessed=now - timedelta(days=2),
        ),
        McpServerRegistry(
            server_id="fresh-safe-low",
            verdict="safe",
            risk_tier="low",
            last_assessed=now - timedelta(days=10),
        ),
        McpServerRegistry(
            server_id="fresh-suspicious-medium",
            verdict="suspicious",
            risk_tier="medium",
            last_assessed=now - timedelta(days=20),
        ),
    ]

    with TestingSessionLocal() as session:
        session.add_all(seeded_rows)
        session.commit()

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/registry/verdict-staleness",
            params={"max_age_days": 30},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["max_age_days"] == 30
        assert payload["generated_at"]
        assert payload["groups"]
        assert sum(group["stale"] for group in payload["groups"]) == 2
        assert sum(group["total"] for group in payload["groups"]) == 5

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    main()