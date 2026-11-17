"""logic.py -- registry trust-band distribution over the real registry data.

Trust bands are fixed as follows:

* ``unknown``: ``trust_score IS NULL``
* ``low``: ``0 <= trust_score < 35``
* ``medium``: ``35 <= trust_score < 65``
* ``high``: ``65 <= trust_score < 85``
* ``critical``: ``trust_score >= 85``
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


BAND_NAMES = ("unknown", "low", "medium", "high", "critical")
BAND_DEFINITIONS = {
    "unknown": "trust_score IS NULL",
    "low": "0 <= trust_score < 35",
    "medium": "35 <= trust_score < 65",
    "high": "65 <= trust_score < 85",
    "critical": "trust_score >= 85",
}


class TrustBand(BaseModel):
    band: str
    count: int
    pct: float
    tier_mix: Dict[str, int] = Field(default_factory=dict)


class RegistryTrustBandBySource(BaseModel):
    registry_source: str
    total: int
    low: int
    medium: int
    high: int
    critical: int
    unknown: int


class RegistryTrustBandResponse(BaseModel):
    total: int
    registry_source: Optional[str]
    bands: List[TrustBand]
    by_source: List[RegistryTrustBandBySource]
    generated_at: str


def _band_for_score(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score < 0:
        return "low"
    if score < 35:
        return "low"
    if score < 65:
        return "medium"
    if score < 85:
        return "high"
    return "critical"


def _source_name(source: Optional[str]) -> str:
    return source if source is not None else "unknown"


def get_registry_trust_bands(
    db: Session,
    registry_source: Optional[str] = None,
    min_score: Optional[float] = None,
) -> Dict[str, object]:
    """Return the registry trust-band distribution.

    ``registry_source`` is an exact-match filter.  When ``min_score`` is
    supplied, only servers whose trust score is at least that value are
    included; consequently, null-score servers are excluded by that filter.
    """
    statement = select(
        McpServerRegistry.registry_source,
        McpServerRegistry.trust_score,
        McpServerRegistry.risk_tier,
    )
    if registry_source is not None:
        statement = statement.where(
            McpServerRegistry.registry_source == registry_source
        )
    if min_score is not None:
        statement = statement.where(
            McpServerRegistry.trust_score.is_not(None),
            McpServerRegistry.trust_score >= min_score,
        )

    rows = db.execute(statement).all()

    band_counts = {band: 0 for band in BAND_NAMES}
    tier_mix: Dict[str, Dict[str, int]] = {
        band: {} for band in BAND_NAMES
    }
    source_counts: Dict[str, Dict[str, int]] = {}

    for source, score, risk_tier in rows:
        band = _band_for_score(score)
        band_counts[band] += 1

        tier = risk_tier or "unknown"
        tier_mix[band][tier] = tier_mix[band].get(tier, 0) + 1

        source_key = _source_name(source)
        if source_key not in source_counts:
            source_counts[source_key] = {name: 0 for name in BAND_NAMES}
        source_counts[source_key][band] += 1

    total = len(rows)
    bands: List[Dict[str, object]] = []
    for band in BAND_NAMES:
        count = band_counts[band]
        pct = round((count / total) * 100, 2) if total else 0.0
        bands.append(
            {
                "band": band,
                "count": count,
                "pct": pct,
                "tier_mix": dict(sorted(tier_mix[band].items())),
            }
        )

    by_source: List[Dict[str, object]] = []
    for source in sorted(source_counts):
        counts = source_counts[source]
        by_source.append(
            {
                "registry_source": source,
                "total": sum(counts.values()),
                "low": counts["low"],
                "medium": counts["medium"],
                "high": counts["high"],
                "critical": counts["critical"],
                "unknown": counts["unknown"],
            }
        )

    return {
        "total": total,
        "registry_source": registry_source,
        "bands": bands,
        "by_source": by_source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# Compatibility names used by service routers and callers.
get_trust_bands = get_registry_trust_bands
get_trust_band_distribution = get_registry_trust_bands
registry_trust_bands = get_registry_trust_bands


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

    seed_rows = [
        ("self-test-unknown", "unknown", None, "low"),
        ("self-test-low", "low", 10.0, "medium"),
        ("self-test-medium", "medium", 50.0, "medium"),
        ("self-test-high", "high", 70.0, "high"),
        ("self-test-critical", "critical", 90.0, "critical"),
        ("self-test-second", "second", 100.0, "low"),
    ]
    with session_factory() as db:
        for server_id, source, score, tier in seed_rows:
            db.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=server_id,
                    registry_source=source,
                    trust_score=score,
                    risk_tier=tier,
                    verdict="test",
                    confidence=1.0,
                    scan_count=1,
                )
            )
        db.commit()

    that_app = FastAPI()

    @that_app.get("/api/registry/trust-bands")
    def trust_bands_route(
        registry_source: Optional[str] = None,
        min_score: Optional[float] = None,
        db: Session = Depends(get_session),
    ):
        return get_registry_trust_bands(db, registry_source, min_score)

    def override_session():
        with session_factory() as db:
            yield db

    that_app.dependency_overrides[get_session] = override_session

    response = TestClient(that_app).get("/api/registry/trust-bands")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 6
    assert sum(item["count"] for item in payload["bands"]) == 6
    unknown = next(item for item in payload["bands"] if item["band"] == "unknown")
    assert unknown["count"] == 1
    assert payload["by_source"]
    assert abs(sum(item["pct"] for item in payload["bands"]) - 100.0) < 0.02
    print("PASS")