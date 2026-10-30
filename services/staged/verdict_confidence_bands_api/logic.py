from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

CONFIDENCE_BANDS: tuple[dict[str, Any], ...] = (
    {"band": "0.0-0.1", "label": "0-10%", "minimum": 0.0, "maximum": 0.1},
    {"band": "0.1-0.25", "label": "10-25%", "minimum": 0.1, "maximum": 0.25},
    {"band": "0.25-0.5", "label": "25-50%", "minimum": 0.25, "maximum": 0.5},
    {"band": "0.5-0.75", "label": "50-75%", "minimum": 0.5, "maximum": 0.75},
    {"band": "0.75-0.9", "label": "75-90%", "minimum": 0.75, "maximum": 0.9},
    {"band": "0.9-1.0", "label": "90-100%", "minimum": 0.9, "maximum": 1.0},
)


def _category(value: Any) -> str:
    if value is None:
        return "unknown"
    normalized = str(value).strip()
    return normalized or "unknown"


def _bucket_for(confidence: Any) -> dict[str, Any] | None:
    if confidence is None:
        return None

    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(value) or value < 0.0 or value > 1.0:
        return None

    for definition in CONFIDENCE_BANDS[:-1]:
        if definition["minimum"] <= value < definition["maximum"]:
            return definition
    return CONFIDENCE_BANDS[-1]


def compute_confidence_bands(session: Session) -> dict[str, Any]:
    statement = select(
        McpServerRegistry.confidence,
        McpServerRegistry.verdict,
        McpServerRegistry.risk_tier,
    )
    rows = session.execute(statement).all()

    verdicts: set[str] = set()
    tiers: set[str] = set()
    for row in rows:
        verdicts.add(_category(row.verdict))
        tiers.add(_category(row.risk_tier))

    bands: list[dict[str, Any]] = []
    for definition in CONFIDENCE_BANDS:
        bands.append(
            {
                "band": definition["band"],
                "label": definition["label"],
                "count": 0,
                "by_verdict": {verdict: 0 for verdict in sorted(verdicts)},
                "by_tier": {tier: 0 for tier in sorted(tiers)},
            }
        )

    for row in rows:
        definition = _bucket_for(row.confidence)
        if definition is None:
            continue

        band = next(item for item in bands if item["band"] == definition["band"])
        verdict = _category(row.verdict)
        tier = _category(row.risk_tier)

        band["count"] += 1
        band["by_verdict"][verdict] = band["by_verdict"].get(verdict, 0) + 1
        band["by_tier"][tier] = band["by_tier"].get(tier, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": len(rows),
        "bands": bands,
    }


def get_confidence_bands(session: Session) -> dict[str, Any]:
    return compute_confidence_bands(session)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
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
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="confidence-test-1",
                    confidence=0.05,
                    verdict="safe",
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="confidence-test-2",
                    confidence=0.4,
                    verdict="caution",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="confidence-test-3",
                    confidence=0.95,
                    verdict="dangerous",
                    risk_tier="high",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/verdict/confidence-bands")
    def confidence_bands(session: Session = Depends(get_session)):
        return compute_confidence_bands(session)

    response = TestClient(that_app).get("/api/verdict/confidence-bands")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 3
    assert sum(band["count"] for band in payload["bands"]) == 3
    top_band = next(
        band for band in payload["bands"] if band["band"] == "0.9-1.0"
    )
    assert top_band["count"] == 1
    print("PASS")