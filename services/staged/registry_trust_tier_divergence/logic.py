"""Trust-score and risk-tier divergence analysis over the real registry."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


TRUST_TIER_DIVERGENCE_THRESHOLD = 0.85

_TRUST_BANDS = (
    (75.0, "high"),
    (45.0, "mid"),
    (20.0, "low"),
)

_RISK_TIER_BANDS = {
    "trusted": "high",
    "caution": "mid",
    "elevated": "low",
    "critical": "minimal",
}


class TrustTierDivergenceRow(BaseModel):
    server_id: str
    name: Optional[str] = None
    trust_score: Optional[float] = None
    risk_tier: Optional[str] = None
    expected_band: str
    divergent: bool


class TrustTierDivergenceResponse(BaseModel):
    servers: List[TrustTierDivergenceRow]
    total: int
    divergent_count: int
    threshold: float


router = APIRouter()


def expected_band_for_trust_score(trust_score: Optional[float]) -> str:
    """Return the canonical trust band for a stored trust score."""
    if trust_score is None:
        return "unknown"

    score = float(trust_score)
    for minimum, band in _TRUST_BANDS:
        if score >= minimum:
            return band
    return "minimal"


def expected_band_for_risk_tier(risk_tier: Optional[str]) -> str:
    """Map a stored risk tier to its canonical trust band."""
    if risk_tier is None:
        return "unknown"
    return _RISK_TIER_BANDS.get(risk_tier.strip().lower(), "unknown")


def compute_trust_tier_divergence(
    db: Session,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Compute divergence rows and aggregate counts from the real registry."""
    records = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
        )
    ).all()

    rows: List[Dict[str, Any]] = []
    for server_id, name, trust_score, risk_tier in records:
        expected_band = expected_band_for_trust_score(trust_score)
        tier_band = expected_band_for_risk_tier(risk_tier)
        divergent = expected_band != "unknown" and tier_band != "unknown" and expected_band != tier_band

        rows.append(
            {
                "server_id": server_id,
                "name": name,
                "trust_score": float(trust_score) if trust_score is not None else None,
                "risk_tier": risk_tier,
                "expected_band": expected_band,
                "divergent": divergent,
            }
        )

    rows.sort(key=lambda row: str(row["server_id"]))
    aggregate = {
        "total": len(rows),
        "divergent_count": sum(1 for row in rows if row["divergent"]),
        "threshold": TRUST_TIER_DIVERGENCE_THRESHOLD,
    }
    return rows, aggregate


def get_trust_tier_divergence(db: Session) -> Dict[str, Any]:
    """Return the API payload for registry trust-tier divergence."""
    rows, aggregate = compute_trust_tier_divergence(db)
    return {"servers": rows, **aggregate}


def get_trust_tier_divergence_rows(db: Session) -> List[Dict[str, Any]]:
    """Return only the per-server divergence rows."""
    rows, _ = compute_trust_tier_divergence(db)
    return rows


@router.get(
    "/registry/trust-tier-divergence",
    response_model=TrustTierDivergenceResponse,
)
def read_trust_tier_divergence(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_trust_tier_divergence(db)


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
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="high-critical",
                    name="High trust, critical tier",
                    trust_score=90,
                    risk_tier="critical",
                ),
                McpServerRegistry(
                    server_id="consistent",
                    name="Consistent registry entry",
                    trust_score=80,
                    risk_tier="trusted",
                ),
                McpServerRegistry(
                    server_id="low-trusted",
                    name="Low trust, trusted tier",
                    trust_score=10,
                    risk_tier="trusted",
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

    with TestClient(test_app) as client:
        response = client.get("/registry/trust-tier-divergence")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert payload["divergent_count"] == 2
        assert any(
            row["server_id"] == "high-critical" and row["divergent"] is True
            for row in payload["servers"]
        )

    print("PASS")