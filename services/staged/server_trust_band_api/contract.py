from collections import Counter
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class TrustBand(BaseModel):
    lo: int
    hi: int
    count: int
    verdicts: Dict[str, int]
    sources: Dict[str, int]


class TrustBandResponse(BaseModel):
    generated_at: datetime
    total: int
    mean_trust_score: float
    bands: List[TrustBand]


def get_trust_band_distribution(session) -> TrustBandResponse:
    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.trust_score,
            McpServerRegistry.verdict,
            McpServerRegistry.registry_source,
        )
    ).all()

    definitions = [
        (0, 20),
        (20, 40),
        (40, 60),
        (60, 80),
        (80, 100),
    ]
    band_rows = {
        definition: {"verdicts": Counter(), "sources": Counter()}
        for definition in definitions
    }
    scores = []

    for row in rows:
        if row.trust_score is None:
            continue

        score = float(row.trust_score)
        scores.append(score)

        if score < 0 or score > 100:
            continue
        if score < 20:
            definition = (0, 20)
        elif score < 40:
            definition = (20, 40)
        elif score < 60:
            definition = (40, 60)
        elif score < 80:
            definition = (60, 80)
        else:
            definition = (80, 100)

        if row.verdict is not None:
            band_rows[definition]["verdicts"][str(row.verdict)] += 1
        if row.registry_source is not None:
            band_rows[definition]["sources"][str(row.registry_source)] += 1

    bands = []
    for lo, hi in definitions:
        counters = band_rows[(lo, hi)]
        bands.append(
            TrustBand(
                lo=lo,
                hi=hi,
                count=sum(counters["verdicts"].values()),
                verdicts=dict(counters["verdicts"]),
                sources=dict(counters["sources"]),
            )
        )

    return TrustBandResponse(
        generated_at=datetime.now(timezone.utc),
        total=len(rows),
        mean_trust_score=sum(scores) / len(scores) if scores else 0.0,
        bands=bands,
    )


router = APIRouter(prefix="/api")


@router.get("/servers/trust-bands", response_model=TrustBandResponse)
def get_trust_bands(session=Depends(get_session)) -> TrustBandResponse:
    return get_trust_band_distribution(session)


app = FastAPI(title="server_trust_band_api")
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    seeded = [
        {
            "server_id": "server-10",
            "name": "Server Ten",
            "registry_source": "catalog-a",
            "url": "https://example.test/10",
            "description": "Acceptance server with trust score 10",
            "trust_score": 10,
            "verdict": "unsafe",
            "verdict_reasoning": "Low trust score",
            "confidence": 0.91,
            "last_assessed": now,
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "critical",
            "meta": {},
        },
        {
            "server_id": "server-45",
            "name": "Server Forty Five",
            "registry_source": "catalog-b",
            "url": "https://example.test/45",
            "description": "Acceptance server with trust score 45",
            "trust_score": 45,
            "verdict": "caution",
            "verdict_reasoning": "Moderate trust score",
            "confidence": 0.82,
            "last_assessed": now,
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "high",
            "meta": {},
        },
        {
            "server_id": "server-65",
            "name": "Server Sixty Five",
            "registry_source": "catalog-c",
            "url": "https://example.test/65",
            "description": "Acceptance server with trust score 65",
            "trust_score": 65,
            "verdict": "trusted",
            "verdict_reasoning": "Good trust score",
            "confidence": 0.88,
            "last_assessed": now,
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "medium",
            "meta": {},
        },
        {
            "server_id": "server-95",
            "name": "Server Ninety Five",
            "registry_source": "catalog-d",
            "url": "https://example.test/95",
            "description": "Acceptance server with trust score 95",
            "trust_score": 95,
            "verdict": "approved",
            "verdict_reasoning": "Very high trust score",
            "confidence": 0.97,
            "last_assessed": now,
            "first_seen": now,
            "last_seen": now,
            "last_scanned": now,
            "scan_count": 1,
            "risk_tier": "low",
            "meta": {},
        },
    ]

    with testing_session() as session:
        session.execute(McpServerRegistry.__table__.insert(), seeded)
        session.commit()

    try:
        with TestClient(app) as client:
            response = client.get("/api/servers/trust-bands")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == 4, payload
            assert payload["mean_trust_score"] == (10 + 45 + 65 + 95) / 4, payload

            bands = {band["lo"]: band for band in payload["bands"]}
            assert bands[80]["count"] == 1, payload
            assert bands[40]["count"] == 1, payload
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()