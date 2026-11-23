"""Data and computation layer for the rescore freshness SLA API."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def band_age(age_days: float) -> str:
    """Return the freshness band for an age in days."""
    if age_days <= 1:
        return "0-1"
    if age_days <= 7:
        return "2-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    return "90+"


def _as_utc(value: datetime) -> datetime:
    """Normalize database timestamps to UTC-aware datetimes."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(value: datetime, as_of: datetime) -> float:
    return max(0.0, (as_of - _as_utc(value)).total_seconds() / 86400.0)


def _isoformat(value: datetime) -> str:
    return _as_utc(value).isoformat()


def rescore_freshness_payload(db: Session) -> Dict[str, object]:
    """Compute freshness bands and the servers with the oldest scores."""
    as_of = datetime.now(timezone.utc)
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
            func.count(McpLlmAxisScore.id).label("axis_count"),
        )
        .outerjoin(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
    ).all()

    band_counts: Dict[str, int] = {
        "0-1": 0,
        "2-7": 0,
        "8-30": 0,
        "31-90": 0,
        "90+": 0,
    }
    worst_offenders: List[Dict[str, object]] = []
    never_scored = 0

    for server_id, name, risk_tier, last_scored_at, axis_count in rows:
        count = int(axis_count or 0)
        if count == 0 or last_scored_at is None:
            never_scored += 1
            continue

        age = _age_days(last_scored_at, as_of)
        band_counts[band_age(age)] += 1
        worst_offenders.append(
            {
                "server_id": str(server_id),
                "name": name or "",
                "risk_tier": risk_tier or "unknown",
                "axis_count": count,
                "last_scored_at": _isoformat(last_scored_at),
                "age_days": age,
            }
        )

    worst_offenders.sort(key=lambda item: item["age_days"], reverse=True)

    return {
        "as_of": as_of.isoformat(),
        "total_servers": len(rows),
        "stale_over_30d": band_counts["31-90"] + band_counts["90+"],
        "never_scored": never_scored,
        "bands": [
            {"band": band, "count": band_counts[band]}
            for band in ("0-1", "2-7", "8-30", "31-90", "90+")
        ],
        "worst_offenders": worst_offenders,
    }


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    db = TestingSession()
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            McpServerRegistry(
                server_id="server-today",
                name="Scored today",
                risk_tier="low",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="server-ten-days",
                name="Scored ten days ago",
                risk_tier="medium",
                registry_source="test",
            ),
            McpServerRegistry(
                server_id="server-never",
                name="Never scored",
                risk_tier="high",
                registry_source="test",
            ),
            McpLlmAxisScore(
                id="score-today",
                server_id="server-today",
                axis_name="trust",
                model_version="test-model",
                scored_at=now,
            ),
            McpLlmAxisScore(
                id="score-ten-days",
                server_id="server-ten-days",
                axis_name="trust",
                model_version="test-model",
                scored_at=now - timedelta(days=10),
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/servers/rescore-freshness")
    def freshness_endpoint(session: Session = Depends(get_session)):
        return rescore_freshness_payload(session)

    try:
        response = TestClient(test_app).get("/api/servers/rescore-freshness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 3
        assert payload["never_scored"] == 1
        assert any(
            item["server_id"] == "server-ten-days" and item["age_days"] >= 9
            for item in payload["worst_offenders"]
        )
        print("PASS")
    finally:
        db.close()
        engine.dispose()


if __name__ == "__main__":
    _self_test()