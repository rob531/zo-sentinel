"""Logic for grouping server verdicts into confidence bands."""

from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def get_confidence_band(db: Session) -> List[Dict[str, Any]]:
    """Return confidence-band counts and average confidence for each verdict."""
    rows = db.execute(
        select(McpServerRegistry.verdict, McpServerRegistry.confidence)
    ).all()

    grouped: Dict[str, Dict[str, Any]] = {}

    for verdict, confidence in rows:
        verdict_key = verdict or "unknown"
        entry = grouped.setdefault(
            verdict_key,
            {
                "verdict": verdict_key,
                "low": 0,
                "medium": 0,
                "high": 0,
                "confidence_sum": 0.0,
                "confidence_count": 0,
                "server_count": 0,
            },
        )
        entry["server_count"] += 1

        if confidence is None:
            continue

        value = float(confidence)
        if value < 0.4:
            entry["low"] += 1
        elif value < 0.7:
            entry["medium"] += 1
        else:
            entry["high"] += 1

        entry["confidence_sum"] += value
        entry["confidence_count"] += 1

    results: List[Dict[str, Any]] = []
    for verdict_key in sorted(grouped):
        entry = grouped[verdict_key]
        confidence_count = entry["confidence_count"]
        results.append(
            {
                "verdict": verdict_key,
                "low": entry["low"],
                "medium": entry["medium"],
                "high": entry["high"],
                "avg_confidence": (
                    entry["confidence_sum"] / confidence_count
                    if confidence_count
                    else 0.0
                ),
                "server_count": entry["server_count"],
            }
        )

    return results


def get_confidence_band_data(db: Session) -> List[Dict[str, Any]]:
    """Compatibility alias for callers using the explicit data-layer name."""
    return get_confidence_band(db)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(server_id="cb-1", verdict="trusted", confidence=0.9),
                McpServerRegistry(server_id="cb-2", verdict="trusted", confidence=0.8),
                McpServerRegistry(server_id="cb-3", verdict="review", confidence=0.5),
                McpServerRegistry(server_id="cb-4", verdict="review", confidence=0.6),
            ]
        )
        db.commit()

    def override_get_session():
        with TestSession() as db:
            yield db

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/verdicts/confidence-band")
    def confidence_band_route(db: Session = Depends(get_session)):
        return {"verdicts": get_confidence_band(db)}

    response = TestClient(that_app).get("/api/verdicts/confidence-band")
    assert response.status_code == 200
    verdicts = {item["verdict"]: item for item in response.json()["verdicts"]}
    assert verdicts["trusted"]["high"] == 2
    assert verdicts["trusted"]["medium"] == 0
    assert verdicts["trusted"]["low"] == 0
    assert abs(verdicts["trusted"]["avg_confidence"] - 0.85) <= 0.01
    assert verdicts["trusted"]["server_count"] == 2
    assert verdicts["review"]["medium"] == 2
    assert abs(verdicts["review"]["avg_confidence"] - 0.55) <= 0.01
    print("PASS")