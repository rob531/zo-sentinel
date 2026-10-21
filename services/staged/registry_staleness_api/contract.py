from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class SourceStaleness(BaseModel):
    registry_source: str
    total: int
    stale: int
    stale_pct: float


class TierStaleness(BaseModel):
    risk_tier: str
    total: int
    stale: int


class StaleServer(BaseModel):
    server_id: str
    name: str
    last_seen: Optional[str]
    age_days: int
    risk_tier: str
    trust_score: float


class RegistryStalenessResponse(BaseModel):
    days: int
    generated_at: str
    stale_count: int
    total_servers: int
    by_source: List[SourceStaleness]
    by_tier: List[TierStaleness]
    stalest: List[StaleServer]


router = APIRouter(prefix="/api")


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    return None


def _text(value: Any, default: str) -> str:
    return default if value is None else str(value)


@router.get(
    "/registry/staleness",
    response_model=RegistryStalenessResponse,
)
def get_registry_staleness(
    days: int = 30,
    limit: int = 50,
    session=Depends(get_session),
) -> RegistryStalenessResponse:
    days = max(1, min(365, days))
    limit = max(1, min(500, limit))

    now = _utc_now_naive()
    cutoff = now - timedelta(days=days)

    columns = (
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.registry_source,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
        McpServerRegistry.risk_tier,
        McpServerRegistry.trust_score,
        McpServerRegistry.verdict,
    )

    all_rows = session.execute(select(*columns)).mappings().all()
    stale_rows = session.execute(
        select(*columns).where(
            or_(
                McpServerRegistry.last_seen < cutoff,
                McpServerRegistry.last_seen.is_(None),
            )
        ),
        {"cutoff": cutoff},
    ).mappings().all()

    stale_ids = {row["server_id"] for row in stale_rows}
    source_totals: Dict[str, int] = {}
    source_stale: Dict[str, int] = {}
    tier_totals: Dict[str, int] = {}
    tier_stale: Dict[str, int] = {}

    for row in all_rows:
        source = _text(row["registry_source"], "unknown")
        tier = _text(row["risk_tier"], "unknown")
        source_totals[source] = source_totals.get(source, 0) + 1
        tier_totals[tier] = tier_totals.get(tier, 0) + 1
        if row["server_id"] in stale_ids:
            source_stale[source] = source_stale.get(source, 0) + 1
            tier_stale[tier] = tier_stale.get(tier, 0) + 1

    by_source = []
    for source in sorted(source_totals):
        total = source_totals[source]
        stale = source_stale.get(source, 0)
        by_source.append(
            SourceStaleness(
                registry_source=source,
                total=total,
                stale=stale,
                stale_pct=round((stale / total) * 100.0, 2) if total else 0.0,
            )
        )

    by_tier = []
    for tier in sorted(tier_totals):
        by_tier.append(
            TierStaleness(
                risk_tier=tier,
                total=tier_totals[tier],
                stale=tier_stale.get(tier, 0),
            )
        )

    stale_servers = []
    for row in stale_rows:
        last_seen = _as_naive_datetime(row["last_seen"])
        if last_seen is None:
            age_days = 0
            last_seen_text = None
        else:
            age_days = max(0, int((now - last_seen).total_seconds() // 86400))
            last_seen_text = last_seen.replace(tzinfo=timezone.utc).isoformat()

        stale_servers.append(
            StaleServer(
                server_id=_text(row["server_id"], ""),
                name=_text(row["name"], ""),
                last_seen=last_seen_text,
                age_days=age_days,
                risk_tier=_text(row["risk_tier"], "unknown"),
                trust_score=float(row["trust_score"] or 0.0),
            )
        )

    stale_servers.sort(key=lambda item: (-item.age_days, item.server_id))

    return RegistryStalenessResponse(
        days=days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        stale_count=len(stale_ids),
        total_servers=len(all_rows),
        by_source=by_source,
        by_tier=by_tier,
        stalest=stale_servers[:limit],
    )


app = FastAPI()
app.include_router(router)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    local_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    db = local_session()
    try:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="aged-server",
                    name="Aged Server",
                    registry_source="source-a",
                    last_seen=datetime.utcnow() - timedelta(days=60),
                    last_scanned=datetime.utcnow() - timedelta(days=60),
                    risk_tier="high",
                    trust_score=0.25,
                    verdict="stale",
                ),
                McpServerRegistry(
                    server_id="fresh-server",
                    name="Fresh Server",
                    registry_source="source-b",
                    last_seen=datetime.utcnow() - timedelta(days=5),
                    last_scanned=datetime.utcnow() - timedelta(days=5),
                    risk_tier="medium",
                    trust_score=0.75,
                    verdict="healthy",
                ),
                McpServerRegistry(
                    server_id="null-server",
                    name="Unknown Server",
                    registry_source="source-c",
                    last_seen=None,
                    last_scanned=None,
                    risk_tier="low",
                    trust_score=0.5,
                    verdict="unknown",
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    def override_get_session():
        session = local_session()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/staleness", params={"days": 30})
        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 30
        assert payload["total_servers"] == 3
        assert payload["stale_count"] == 2
        assert len(payload["by_source"]) == 3
        assert len(payload["by_tier"]) == 3
        assert payload["stalest"][0]["age_days"] >= payload["stalest"][1]["age_days"]
        assert {item["server_id"] for item in payload["stalest"]} == {
            "aged-server",
            "null-server",
        }

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()