"""HTTP surface for registry trust-band distribution."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_trust_band_distribution

router = APIRouter(prefix="/api", tags=["registry-trust-band"])


class TrustBand(BaseModel):
    band: str
    count: int
    pct: float
    tier_mix: dict[str, int]


class SourceTrustBands(BaseModel):
    registry_source: str
    total: int
    low: int
    medium: int
    high: int
    critical: int
    unknown: int


class TrustBandResponse(BaseModel):
    total: int
    registry_source: str | None
    bands: list[TrustBand]
    by_source: list[SourceTrustBands]
    generated_at: str


@router.get("/registry/trust-bands", response_model=TrustBandResponse)
def trust_bands(
    db: Session = Depends(get_session),
    registry_source: str | None = Query(default=None),
    min_score: float | None = Query(default=None),
) -> TrustBandResponse:
    return get_trust_band_distribution(
        db,
        registry_source=registry_source,
        min_score=min_score,
    )


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
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    rows = [
        McpServerRegistry(
            server_id="registry-unknown",
            name="unknown",
            registry_source="alpha",
            trust_score=None,
            risk_tier="high",
        ),
        McpServerRegistry(
            server_id="registry-low-1",
            name="low-1",
            registry_source="alpha",
            trust_score=10.0,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="registry-low-2",
            name="low-2",
            registry_source="beta",
            trust_score=34.9,
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="registry-medium",
            name="medium",
            registry_source="beta",
            trust_score=35.0,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="registry-high",
            name="high",
            registry_source="alpha",
            trust_score=65.0,
            risk_tier="critical",
        ),
        McpServerRegistry(
            server_id="registry-critical",
            name="critical",
            registry_source="beta",
            trust_score=85.0,
            risk_tier="critical",
        ),
    ]
    with session_factory() as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/trust-bands")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 6, payload
    assert sum(band["count"] for band in payload["bands"]) == 6, payload
    assert next(band for band in payload["bands"] if band["band"] == "unknown")["count"] == 1
    assert payload["by_source"], payload
    assert abs(sum(band["pct"] for band in payload["bands"]) - 100.0) < 0.02, payload
    print("PASS")


if __name__ == "__main__":
    _self_test()