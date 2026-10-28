from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import McpServerRegistry


class SourceTierCount(BaseModel):
    source: str
    tier: str
    count: int


class TierMixResponse(BaseModel):
    total: int
    by_tier: Dict[str, int]
    by_source_tier: List[SourceTierCount]
    generated_at: str


def get_tier_mix(
    session: Session = Depends(get_session),
    source: Optional[str] = Query(default=None),
    days: int = Query(default=90, ge=0),
) -> TierMixResponse:
    cutoff = datetime.utcnow() - timedelta(days=days)

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
        McpServerRegistry.registry_source,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_assessed,
    ).where(McpServerRegistry.last_seen >= cutoff)

    if source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == source
        )

    rows = session.execute(statement).all()

    by_tier: Dict[str, int] = {}
    by_source_tier: Dict[tuple[str, str], int] = {}

    for row in rows:
        tier = row.risk_tier or "UNSCORED"
        source_name = row.registry_source or ""
        by_tier[tier] = by_tier.get(tier, 0) + 1
        key = (source_name, tier)
        by_source_tier[key] = by_source_tier.get(key, 0) + 1

    grouped = [
        SourceTierCount(source=source_name, tier=tier, count=count)
        for (source_name, tier), count in sorted(by_source_tier.items())
    ]

    return TierMixResponse(
        total=len(rows),
        by_tier=by_tier,
        by_source_tier=grouped,
        generated_at=datetime.utcnow().isoformat() + "Z",
    )


router = APIRouter()
router.get(
    "/registry/tier-mix",
    response_model=TierMixResponse,
    summary="Registry tier mix",
)(get_tier_mix)

app = FastAPI(title="Registry Tier Mix API")
app.include_router(router, prefix="/api")


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    seed_rows = [
        {
            "server_id": "registry-test-1",
            "verdict": "TRUSTED",
            "risk_tier": "TRUSTED_GENERAL",
            "registry_source": "registry-a",
            "last_seen": now,
            "last_assessed": now,
        },
        {
            "server_id": "registry-test-2",
            "verdict": "REVIEW",
            "risk_tier": "REVIEW",
            "registry_source": "registry-a",
            "last_seen": now,
            "last_assessed": now,
        },
        {
            "server_id": "registry-test-3",
            "verdict": "REJECT",
            "risk_tier": "REJECTED",
            "registry_source": "registry-b",
            "last_seen": now,
            "last_assessed": now,
        },
        {
            "server_id": "registry-test-4",
            "verdict": "UNKNOWN",
            "risk_tier": None,
            "registry_source": "registry-b",
            "last_seen": now,
            "last_assessed": now,
        },
        {
            "server_id": "registry-test-5",
            "verdict": "TRUSTED",
            "risk_tier": "TRUSTED_GENERAL",
            "registry_source": "registry-b",
            "last_seen": now,
            "last_assessed": now,
        },
    ]

    session = TestingSession()
    try:
        for row in seed_rows:
            session.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                    (server_id, verdict, risk_tier, registry_source, last_seen, last_assessed)
                    VALUES
                    (:server_id, :verdict, :risk_tier, :registry_source, :last_seen, :last_assessed)
                    """
                ),
                row,
            )
        session.commit()
    finally:
        session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        db_session = TestingSession()
        try:
            yield db_session
        finally:
            db_session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/tier-mix")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == len(seed_rows), payload
            assert payload["by_tier"]["UNSCORED"] == 1, payload
            assert payload["by_source_tier"] == [
                {"source": "registry-a", "tier": "REVIEW", "count": 1},
                {"source": "registry-a", "tier": "TRUSTED_GENERAL", "count": 1},
                {"source": "registry-b", "tier": "REJECTED", "count": 1},
                {"source": "registry-b", "tier": "TRUSTED_GENERAL", "count": 1},
                {"source": "registry-b", "tier": "UNSCORED", "count": 1},
            ], payload
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()