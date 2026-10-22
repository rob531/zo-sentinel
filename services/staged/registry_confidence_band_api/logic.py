from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_BANDS = (
    "0.0-0.2",
    "0.2-0.4",
    "0.4-0.6",
    "0.6-0.8",
    "0.8-1.0",
)


def _confidence_band_expression():
    confidence = McpServerRegistry.confidence
    return case(
        (and_(confidence >= 0.0, confidence < 0.2), "0.0-0.2"),
        (and_(confidence >= 0.2, confidence < 0.4), "0.2-0.4"),
        (and_(confidence >= 0.4, confidence < 0.6), "0.4-0.6"),
        (and_(confidence >= 0.6, confidence < 0.8), "0.6-0.8"),
        else_="0.8-1.0",
    )


def get_confidence_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    band_expression = _confidence_band_expression()

    rows = db.execute(
        select(
            band_expression.label("band"),
            McpServerRegistry.risk_tier.label("risk_tier"),
            func.count().label("server_count"),
        )
        .where(McpServerRegistry.confidence.is_not(None))
        .group_by(band_expression, McpServerRegistry.risk_tier)
    ).all()

    counts = {band: 0 for band in _BANDS}
    by_tier: Dict[str, Dict[str, int]] = {band: {} for band in _BANDS}

    for row in rows:
        band = row.band
        if band not in counts:
            continue
        tier = row.risk_tier
        tier_key = "unknown" if tier is None else str(tier)
        tier_count = int(row.server_count)
        counts[band] += tier_count
        by_tier[band][tier_key] = by_tier[band].get(tier_key, 0) + tier_count

    null_confidence = int(
        db.execute(
            select(func.count())
            .select_from(McpServerRegistry)
            .where(McpServerRegistry.confidence.is_(None))
        ).scalar_one()
    )

    bands: List[Dict[str, Any]] = [
        {
            "band": band,
            "count": counts[band],
            "by_tier": by_tier[band],
        }
        for band in _BANDS
    ]

    return {
        "total": sum(counts.values()) + null_confidence,
        "null_confidence": null_confidence,
        "bands": bands,
    }


def get_registry_confidence_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_confidence_bands(db)


def get_confidence_band_distribution(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_confidence_bands(db)


def compute_confidence_bands(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_confidence_bands(db)


def _self_test() -> None:
    from datetime import datetime, timezone

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
    session_factory = sessionmaker(bind=engine)

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/registry/confidence-bands")
    def confidence_bands(
        db: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_confidence_bands(db)

    rows = [
        ("confidence-1", "Server One", "trusted", "low", 0.1),
        ("confidence-2", "Server Two", "trusted", "medium", 0.35),
        ("confidence-3", "Server Three", "caution", "high", 0.5),
        ("confidence-4", "Server Four", "caution", "critical", 0.9),
        ("confidence-5", "Server Five", "unknown", "low", None),
    ]

    with session_factory() as session:
        for server_id, name, verdict, risk_tier, confidence in rows:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=name,
                    verdict=verdict,
                    risk_tier=risk_tier,
                    confidence=confidence,
                    last_assessed=datetime.now(timezone.utc),
                )
            )
        session.commit()

    with TestClient(that_app) as client:
        response = client.get("/api/registry/confidence-bands")
        assert response.status_code == 200
        payload = response.json()
        assert [band["count"] for band in payload["bands"]] == [1, 1, 1, 0, 1]
        assert payload["null_confidence"] == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()