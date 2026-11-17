from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import compute_confidence_bands

router = APIRouter(prefix="/api/registry", tags=["registry"])


class ConfidenceBandResponse(BaseModel):
    band: str
    count: int
    mean_trust_score: float | None
    verdict_counts: dict[str, int]


class VerdictConfidenceBandsResponse(BaseModel):
    bands: list[ConfidenceBandResponse]
    total: int
    generated_at: str


@router.get(
    "/verdict-confidence-bands",
    response_model=VerdictConfidenceBandsResponse,
)
def get_verdict_confidence_bands(
    db: Session = Depends(get_session),
) -> VerdictConfidenceBandsResponse:
    rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.confidence,
            McpServerRegistry.trust_score,
            McpServerRegistry.risk_tier,
        )
    ).mappings().all()

    computed_bands = compute_confidence_bands([dict(row) for row in rows])
    bands = [
        ConfidenceBandResponse(
            band=band,
            count=stats["count"],
            mean_trust_score=stats["mean_trust_score"],
            verdict_counts=stats["verdict_counts"],
        )
        for band, stats in computed_bands.items()
    ]

    return VerdictConfidenceBandsResponse(
        bands=bands,
        total=sum(band.count for band in bands),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
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
    TestingSessionLocal = sessionmaker(bind=engine)

    testing_db = TestingSessionLocal()
    testing_db.add_all(
        [
            McpServerRegistry(
                server_id="confidence-test-1",
                verdict="TRUSTED_GENERAL",
                confidence=0.1,
                trust_score=0.8,
                risk_tier="LOW",
            ),
            McpServerRegistry(
                server_id="confidence-test-2",
                verdict="TRUSTED_GENERAL",
                confidence=0.4,
                trust_score=0.7,
                risk_tier="MEDIUM",
            ),
            McpServerRegistry(
                server_id="confidence-test-3",
                verdict="HIGH_RISK_ISOLATED",
                confidence=0.6,
                trust_score=0.4,
                risk_tier="HIGH",
            ),
            McpServerRegistry(
                server_id="confidence-test-4",
                verdict="HIGH_RISK_ISOLATED",
                confidence=0.9,
                trust_score=0.2,
                risk_tier="CRITICAL",
            ),
        ]
    )
    testing_db.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: testing_db

    with TestClient(that_app) as client:
        response = client.get("/api/registry/verdict-confidence-bands")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 4, payload
        assert sum(band["count"] for band in payload["bands"]) == 4, payload
        assert any(
            band["band"] == "0.50-0.75" and band["count"] == 1
            for band in payload["bands"]
        ), payload

    testing_db.close()
    engine.dispose()
    print("PASS")