from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class PercentileStats(BaseModel):
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    min: float
    max: float
    mean: float


class VerdictPercentile(BaseModel):
    verdict: str
    count: int
    p50: float


class ConfidencePercentileResponse(BaseModel):
    count: int
    overall: PercentileStats
    by_verdict: list[VerdictPercentile]


def _percentile_stats(values: list[float]) -> PercentileStats:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("at least one value is required")

    if len(ordered) == 1:
        value = float(ordered[0])
        return PercentileStats(
            p10=value,
            p25=value,
            p50=value,
            p75=value,
            p90=value,
            min=value,
            max=value,
            mean=value,
        )

    quantiles = __import__("statistics").quantiles(ordered, n=100, method="inclusive")
    return PercentileStats(
        p10=float(quantiles[9]),
        p25=float(quantiles[24]),
        p50=float(__import__("statistics").median(ordered)),
        p75=float(quantiles[74]),
        p90=float(quantiles[89]),
        min=float(ordered[0]),
        max=float(ordered[-1]),
        mean=float(__import__("statistics").mean(ordered)),
    )


def get_confidence_percentiles(session: Session = Depends(get_session)) -> ConfidencePercentileResponse:
    rows = session.execute(
        select(McpServerRegistry.confidence, McpServerRegistry.verdict)
    ).all()

    confidence_values: list[float] = []
    verdict_values: dict[str, list[float]] = {}

    for confidence, verdict in rows:
        if confidence is None:
            continue
        numeric_confidence = float(confidence)
        confidence_values.append(numeric_confidence)
        if verdict is not None:
            verdict_values.setdefault(str(verdict), []).append(numeric_confidence)

    by_verdict = [
        VerdictPercentile(
            verdict=verdict,
            count=len(values),
            p50=float(__import__("statistics").median(values)),
        )
        for verdict, values in sorted(verdict_values.items())
    ]

    return ConfidencePercentileResponse(
        count=len(confidence_values),
        overall=_percentile_stats(confidence_values),
        by_verdict=by_verdict,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/registry/confidence-percentiles",
    response_model=ConfidencePercentileResponse,
)
def confidence_percentiles(
    session: Session = Depends(get_session),
) -> ConfidencePercentileResponse:
    return get_confidence_percentiles(session)


app = FastAPI(title="registry_confidence_percentile_api")
app.include_router(router)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    verdicts = ("approved", "review", "blocked")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    seeded: list[dict[str, Any]] = []

    for index in range(30):
        verdict = verdicts[index % len(verdicts)]
        confidence = 0.10 + (index * 0.027) % 0.90
        seeded.append(
            {
                "server_id": f"contract-server-{index:02d}",
                "name": f"Contract Server {index:02d}",
                "verdict": verdict,
                "risk_tier": ("low", "medium", "high")[index % 3],
                "confidence": confidence,
                "trust_score": 1.0 - confidence,
                "url": f"https://example.invalid/contract/{index}",
                "description": "Acceptance-test registry record",
                "first_seen": now,
                "last_assessed": now,
                "last_seen": now,
                "last_scanned": now,
                "scan_count": index,
                "registry_source": "contract_test",
                "verdict_reasoning": "Seeded for contract acceptance",
                "meta": {},
            }
        )

    with TestingSessionLocal() as session:
        session.execute(insert(McpServerRegistry.__table__), seeded)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/confidence-percentiles")
            assert response.status_code == 200, response.text
            payload = response.json()

        assert payload["count"] == len(seeded), payload
        assert payload["overall"]["min"] <= payload["overall"]["p50"] <= payload["overall"]["max"], payload
        assert {entry["verdict"] for entry in payload["by_verdict"]} == set(verdicts), payload
        assert all(entry["count"] > 0 for entry in payload["by_verdict"]), payload
    finally:
        test_app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()