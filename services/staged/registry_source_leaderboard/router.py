from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import compute_leaderboard

router = APIRouter(prefix="/api", tags=["registry"])


class SourceLeaderboardRow(BaseModel):
    source: str
    server_count: int
    mean_trust_score: Optional[float]
    mean_confidence: Optional[float]
    tiers: Dict[str, int]
    last_seen: Optional[str]


class SourceLeaderboardResponse(BaseModel):
    generated_at: str
    total_sources: int
    rows: List[SourceLeaderboardRow]


def _timestamp(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _mean(values: List[float]) -> Optional[float]:
    if not values:
        return None
    return sum(values) / len(values)


def _leaderboard_from_session(session: Session, limit: int) -> List[Dict[str, object]]:
    aggregates: Dict[str, Dict[str, object]] = {}

    for record in session.query(McpServerRegistry).all():
        source = record.registry_source or "unknown"
        aggregate = aggregates.setdefault(
            source,
            {
                "server_count": 0,
                "trust_scores": [],
                "confidences": [],
                "tiers": Counter(),
                "last_seen": None,
            },
        )
        aggregate["server_count"] += 1

        if record.trust_score is not None:
            aggregate["trust_scores"].append(float(record.trust_score))
        if record.confidence is not None:
            aggregate["confidences"].append(float(record.confidence))

        verdict = record.verdict or "unknown"
        aggregate["tiers"][verdict] += 1

        seen = _timestamp(record.last_seen)
        if seen is not None:
            previous = aggregate["last_seen"]
            if previous is None or seen > previous:
                aggregate["last_seen"] = seen

    rows = []
    for source, aggregate in aggregates.items():
        rows.append(
            {
                "source": source,
                "server_count": aggregate["server_count"],
                "mean_trust_score": _mean(aggregate["trust_scores"]),
                "mean_confidence": _mean(aggregate["confidences"]),
                "tiers": dict(sorted(aggregate["tiers"].items())),
                "last_seen": aggregate["last_seen"],
            }
        )

    rows.sort(key=lambda row: (-row["server_count"], row["source"]))
    return rows[:limit]


@router.get(
    "/registry/sources/leaderboard",
    response_model=SourceLeaderboardResponse,
    summary="Registry-source server leaderboard",
)
def registry_source_leaderboard(
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> SourceLeaderboardResponse:
    rows = _leaderboard_from_session(session, limit)
    return SourceLeaderboardResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_sources=len(rows),
        rows=[SourceLeaderboardRow(**row) for row in rows],
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="official-1",
                    name="Official One",
                    registry_source="official",
                    trust_score=0.95,
                    confidence=0.9,
                    verdict="trusted",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="official-2",
                    name="Official Two",
                    registry_source="official",
                    trust_score=0.85,
                    confidence=0.8,
                    verdict="trusted",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="community-1",
                    name="Community One",
                    registry_source="community",
                    trust_score=0.7,
                    confidence=0.75,
                    verdict="review",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="unknown-1",
                    name="Unknown Trust Source",
                    registry_source="experimental",
                    trust_score=None,
                    confidence=0.5,
                    verdict="unknown",
                    risk_tier="unknown",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/registry/sources/leaderboard",
        params={"limit": 50},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["rows"]) == 3, payload
    assert payload["rows"][0]["source"] == "official", payload
    assert payload["rows"][0]["server_count"] == 2, payload
    assert next(row for row in payload["rows"] if row["source"] == "experimental")[
        "mean_trust_score"
    ] is None, payload
    print("PASS")