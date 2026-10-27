from __future__ import annotations

from datetime import datetime, timezone
import inspect

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import get_verdict_confidence_bands
except ModuleNotFoundError as exc:
    if exc.name != f"{__package__}.logic":
        raise

    def get_verdict_confidence_bands(session: Session) -> dict:
        band = case(
            (McpServerRegistry.confidence.is_(None), "unknown"),
            (McpServerRegistry.confidence < 0.20, "lt_20"),
            (McpServerRegistry.confidence < 0.40, "20_40"),
            (McpServerRegistry.confidence < 0.60, "40_60"),
            (McpServerRegistry.confidence < 0.80, "60_80"),
            else_="gte_80",
        )

        rows = session.execute(
            select(McpServerRegistry.verdict, band.label("band"), func.count().label("count"))
            .select_from(McpServerRegistry)
            .group_by(McpServerRegistry.verdict, band)
        ).all()

        bands = [
            {"band": row.band, "verdict": row.verdict or "unknown", "count": int(row.count)}
            for row in rows
        ]
        bands.sort(key=lambda item: (item["band"], item["verdict"]))

        return {
            "total": sum(item["count"] for item in bands),
            "bands": bands,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }


router = APIRouter(prefix="/api")


class BandCount(BaseModel):
    band: str
    verdict: str
    count: int


class VerdictConfidenceBandResponse(BaseModel):
    total: int
    bands: list[BandCount]
    generated_at: str


@router.get(
    "/registry/verdict-confidence-bands",
    response_model=VerdictConfidenceBandResponse,
)
async def verdict_confidence_bands(
    session: Session = Depends(get_session),
) -> VerdictConfidenceBandResponse:
    result = get_verdict_confidence_bands(session)
    if inspect.isawaitable(result):
        result = await result

    if isinstance(result, dict):
        payload = dict(result)
    else:
        payload = {"bands": result}

    payload.setdefault("total", sum(int(item["count"]) for item in payload.get("bands", [])))
    payload.setdefault("bands", [])
    payload.setdefault("generated_at", datetime.now(timezone.utc).isoformat())

    if isinstance(payload["generated_at"], datetime):
        payload["generated_at"] = payload["generated_at"].isoformat()

    return VerdictConfidenceBandResponse(**payload)


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
    TestSession = sessionmaker(bind=test_engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="contract-1",
                    name="trusted-general",
                    verdict="TRUSTED_GENERAL",
                    confidence=0.91,
                    registry_source="contract",
                    risk_tier="LOW",
                ),
                McpServerRegistry(
                    server_id="contract-2",
                    name="high-risk-isolated",
                    verdict="HIGH_RISK_ISOLATED",
                    confidence=0.22,
                    registry_source="contract",
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id="contract-3",
                    name="caution-limited",
                    verdict="CAUTION_LIMITED",
                    confidence=0.55,
                    registry_source="contract",
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id="contract-4",
                    name="unknown-confidence",
                    verdict="UNVERIFIED",
                    confidence=None,
                    registry_source="contract",
                    risk_tier="UNKNOWN",
                ),
                McpServerRegistry(
                    server_id="contract-5",
                    name="low-confidence",
                    verdict="LOW_RISK",
                    confidence=0.05,
                    registry_source="contract",
                    risk_tier="LOW",
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get(
        "/api/registry/verdict-confidence-bands"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert any(
        item["band"] == "gte_80"
        and item["verdict"] == "TRUSTED_GENERAL"
        and item["count"] == 1
        for item in payload["bands"]
    )
    assert any(
        item["band"] == "unknown" and item["count"] == 1
        for item in payload["bands"]
    )
    print("PASS")