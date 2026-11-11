from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Float, cast, func, literal, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class SourceLeaderboardRow(BaseModel):
    source: str
    server_count: int
    mean_trust_score: float | None
    mean_confidence: float | None
    tiers: dict[str, int]
    last_seen: str | None


class SourceLeaderboardResponse(BaseModel):
    generated_at: str
    total_sources: int
    rows: list[SourceLeaderboardRow]


router = APIRouter(prefix="/api")


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def compute_leaderboard(
    limit: int = Query(50, ge=1),
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    limit = max(1, int(limit))
    source = func.coalesce(McpServerRegistry.registry_source, literal("unknown"))
    verdict = func.coalesce(McpServerRegistry.verdict, literal("unknown"))

    summary_query = (
        select(
            source.label("source"),
            func.count(McpServerRegistry.server_id).label("server_count"),
            func.avg(cast(McpServerRegistry.trust_score, Float)).label(
                "mean_trust_score"
            ),
            func.avg(cast(McpServerRegistry.confidence, Float)).label(
                "mean_confidence"
            ),
            func.max(McpServerRegistry.last_seen).label("last_seen"),
        )
        .group_by(source)
        .order_by(func.count(McpServerRegistry.server_id).desc(), source.asc())
    )
    summary_rows = session.execute(summary_query).all()

    verdict_query = (
        select(
            source.label("source"),
            verdict.label("verdict"),
            func.count(McpServerRegistry.server_id).label("verdict_count"),
        )
        .group_by(source, verdict)
    )
    verdict_rows = session.execute(verdict_query).all()

    tiers_by_source: dict[str, dict[str, int]] = {}
    for row in verdict_rows:
        tiers_by_source.setdefault(str(row.source), {})[str(row.verdict)] = int(
            row.verdict_count
        )

    rows: list[dict[str, Any]] = []
    for row in summary_rows:
        rows.append(
            {
                "source": str(row.source),
                "server_count": int(row.server_count),
                "mean_trust_score": (
                    float(row.mean_trust_score)
                    if row.mean_trust_score is not None
                    else None
                ),
                "mean_confidence": (
                    float(row.mean_confidence)
                    if row.mean_confidence is not None
                    else None
                ),
                "tiers": tiers_by_source.get(str(row.source), {}),
                "last_seen": _as_iso(row.last_seen),
            }
        )

    return rows[:limit]


@router.get(
    "/registry/sources/leaderboard",
    response_model=SourceLeaderboardResponse,
)
def registry_source_leaderboard(
    limit: int = Query(50, ge=1),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = compute_leaderboard(limit=limit, session=session)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_sources": len(rows),
        "rows": rows,
    }


def _self_test() -> None:
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
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="official-1",
                    name="Official One",
                    registry_source="official",
                    trust_score=0.95,
                    verdict="trusted",
                    confidence=0.9,
                    risk_tier="A",
                    last_seen=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="official-2",
                    name="Official Two",
                    registry_source="official",
                    trust_score=0.85,
                    verdict="trusted",
                    confidence=0.8,
                    risk_tier="A",
                    last_seen=datetime(2024, 1, 2, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="community-1",
                    name="Community One",
                    registry_source="community",
                    trust_score=0.5,
                    verdict="review",
                    confidence=0.6,
                    risk_tier="B",
                    last_seen=datetime(2024, 1, 3, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="unknown-1",
                    name="Unknown One",
                    registry_source="unknown",
                    trust_score=None,
                    verdict=None,
                    confidence=None,
                    risk_tier=None,
                    last_seen=None,
                ),
            ]
        )
        session.commit()

        def override_session():
            return session

        that_app = FastAPI()
        that_app.dependency_overrides[get_session] = override_session
        that_app.include_router(router)

        response = TestClient(that_app).get(
            "/api/registry/sources/leaderboard", params={"limit": 50}
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["rows"]) == 3
        assert payload["rows"][0]["source"] == "official"
        assert payload["rows"][0]["server_count"] == 2
        unknown = next(row for row in payload["rows"] if row["source"] == "unknown")
        assert unknown["mean_trust_score"] is None
    finally:
        session.close()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()