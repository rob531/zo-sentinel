from collections import OrderedDict
import math
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


CONFIDENCE_BANDS = (
    "0.0-0.2",
    "0.2-0.4",
    "0.4-0.6",
    "0.6-0.8",
    "0.8-1.0",
    "unknown",
)

LOW_CONFIDENCE_BANDS = frozenset({"0.0-0.2", "0.2-0.4"})


def band_confidence(value: float | None) -> str:
    if value is None:
        return "unknown"

    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return "unknown"

    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
        return "unknown"
    if confidence < 0.2:
        return "0.0-0.2"
    if confidence < 0.4:
        return "0.2-0.4"
    if confidence < 0.6:
        return "0.4-0.6"
    if confidence < 0.8:
        return "0.6-0.8"
    return "0.8-1.0"


def compute_bands(rows: list[dict]) -> list[dict]:
    counts = OrderedDict((band, 0) for band in CONFIDENCE_BANDS)
    tiers: dict[str, dict[str, int]] = {
        band: OrderedDict() for band in CONFIDENCE_BANDS
    }

    for row in rows:
        band = band_confidence(row.get("confidence"))
        tier = row.get("risk_tier")
        tier_name = str(tier) if tier is not None else "unknown"

        counts[band] += 1
        tier_counts = tiers[band]
        tier_counts[tier_name] = tier_counts.get(tier_name, 0) + 1

    return [
        {
            "band": band,
            "count": counts[band],
            "by_tier": tiers[band],
        }
        for band in CONFIDENCE_BANDS
    ]


def get_confidence_bands(session: Session = Depends(get_session)) -> dict[str, Any]:
    statement = select(
        McpServerRegistry.confidence,
        McpServerRegistry.risk_tier,
    )
    rows = [
        {
            "confidence": row.confidence,
            "risk_tier": row.risk_tier,
        }
        for row in session.execute(statement)
    ]

    bands = compute_bands(rows)
    return {
        "total": len(rows),
        "bands": bands,
        "low_confidence_count": sum(
            band["count"]
            for band in bands
            if band["band"] in LOW_CONFIDENCE_BANDS
        ),
    }


get_registry_confidence_bands = get_confidence_bands


if __name__ == "__main__":
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
    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="confidence-test-1",
                    confidence=0.1,
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="confidence-test-2",
                    confidence=0.35,
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="confidence-test-3",
                    confidence=0.65,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="confidence-test-4",
                    confidence=0.95,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="confidence-test-5",
                    confidence=None,
                    risk_tier="unknown",
                ),
            ]
        )
        session.commit()

        def override_get_session():
            yield session

        local_app = FastAPI()

        @local_app.get("/api/registry/confidence-bands")
        def confidence_bands_route(
            db: Session = Depends(get_session),
        ) -> dict[str, Any]:
            return get_confidence_bands(db)

        local_app.dependency_overrides[get_session] = override_get_session

        with TestClient(local_app) as client:
            response = client.get("/api/registry/confidence-bands")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 5
            assert sum(band["count"] for band in payload["bands"]) == 5
            assert all(
                sum(band["by_tier"].values()) == band["count"]
                for band in payload["bands"]
            )
            unknown = next(
                band for band in payload["bands"] if band["band"] == "unknown"
            )
            assert unknown["count"] == 1
            assert payload["low_confidence_count"] == 2

    test_engine.dispose()
    print("PASS")