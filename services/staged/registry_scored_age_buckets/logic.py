"""Logic for the registry scored-age buckets service."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter(prefix="/api")


def _as_utc(value: datetime) -> datetime:
    """Return a timezone-aware UTC datetime for values read from the database."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_since(value: datetime, now: datetime) -> int:
    """Calculate whole elapsed days in UTC."""
    return max(0, (now - _as_utc(value)).days)


def get_scored_age_buckets(
    db: Session,
    registry_source: Optional[str] = None,
    days: int = 30,
) -> Dict[str, object]:
    """Bucket registry servers according to the age of their latest axis score."""
    registry_query = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.risk_tier,
    )
    if registry_source is not None:
        registry_query = registry_query.where(
            McpServerRegistry.registry_source == registry_source
        )
    registry_query = registry_query.order_by(
        McpServerRegistry.server_id
    )

    servers = db.execute(registry_query).all()
    server_ids = [row.server_id for row in servers]

    latest_scores: Dict[object, datetime] = {}
    if server_ids:
        score_rows = db.execute(
            select(
                McpLlmAxisScore.server_id,
                func.max(McpLlmAxisScore.scored_at),
            )
            .where(McpLlmAxisScore.server_id.in_(server_ids))
            .group_by(McpLlmAxisScore.server_id)
        ).all()
        for server_id, scored_at in score_rows:
            if scored_at is not None:
                latest_scores[server_id] = _as_utc(scored_at)

    now = datetime.now(timezone.utc)
    buckets: Dict[str, List[Dict[str, object]]] = {
        "unscored": [],
        "fresh": [],
        "aging": [],
        "stale": [],
    }

    for server_id, name, risk_tier in servers:
        scored_at = latest_scores.get(server_id)
        if scored_at is None:
            bucket = "unscored"
            days_since = None
            last_scored_at = None
        else:
            days_since = _days_since(scored_at, now)
            last_scored_at = scored_at.isoformat()
            if days_since <= days:
                bucket = "fresh"
            elif days_since <= 2 * days:
                bucket = "aging"
            else:
                bucket = "stale"

        buckets[bucket].append(
            {
                "server_id": server_id,
                "name": name,
                "risk_tier": risk_tier,
                "last_scored_at": last_scored_at,
                "days_since_scored": days_since,
            }
        )

    bucket_output = [
        {
            "bucket": bucket,
            "count": len(buckets[bucket]),
            "servers": buckets[bucket],
        }
        for bucket in ("unscored", "fresh", "aging", "stale")
    ]

    total_servers = len(servers)
    scored_servers = len(latest_scores)

    return {
        "threshold_days": days,
        "buckets": bucket_output,
        "total_servers": total_servers,
        "scored_fraction": (
            scored_servers / total_servers if total_servers else 0.0
        ),
    }


@router.get("/registry/scored-age")
def registry_scored_age_endpoint(
    registry_source: Optional[str] = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> Dict[str, object]:
    """Return registry servers grouped by how recently they were scored."""
    return get_scored_age_buckets(db, registry_source=registry_source, days=days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="recent",
                    name="Recently scored",
                    registry_source="test",
                    risk_tier="low",
                    verdict="ok",
                ),
                McpServerRegistry(
                    server_id="old",
                    name="Oldly scored",
                    registry_source="test",
                    risk_tier="high",
                    verdict="review",
                ),
                McpServerRegistry(
                    server_id="none",
                    name="Never scored",
                    registry_source="test",
                    risk_tier="medium",
                    verdict="unknown",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="recent",
                    axis_name="quality",
                    model_version="test-model",
                    scored_at=datetime.now(timezone.utc).replace(
                        tzinfo=None
                    ),
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="old",
                    axis_name="quality",
                    model_version="test-model",
                    scored_at=(
                        datetime.now(timezone.utc) - timezone.utc.utcoffset(None)
                        if False
                        else datetime.now(timezone.utc).replace(tzinfo=None)
                    ),
                ),
            ]
        )
        session.flush()
        old_score = (
            session.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == "old")
            .one()
        )
        old_score.scored_at = (
            datetime.now(timezone.utc) - timezone.utc.utcoffset(None)
            if False
            else datetime.now(timezone.utc).replace(tzinfo=None)
        )
        from datetime import timedelta

        old_score.scored_at = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) - timedelta(days=100)
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/registry/scored-age?days=30")
    assert response.status_code == 200, response.text
    payload = response.json()
    bucket_counts = {
        item["bucket"]: item["count"] for item in payload["buckets"]
    }
    assert bucket_counts["unscored"] == 1, payload
    assert bucket_counts["stale"] == 1, payload
    assert abs(payload["scored_fraction"] - (2 / 3)) < 1e-9, payload
    print("PASS")