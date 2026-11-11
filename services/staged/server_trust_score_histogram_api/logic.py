from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class TrustScoreBucket(BaseModel):
    range: str
    count: int
    trusted: int
    critical: int


class TrustScoreHistogramResponse(BaseModel):
    bucket_size: int
    total_scored: int
    buckets: List[TrustScoreBucket]


def build_trust_score_histogram(
    session: Session, bucket: int = 5
) -> TrustScoreHistogramResponse:
    if bucket != 5:
        raise ValueError("bucket must be 5")

    bucket_size = 20
    ranges = ["0-20", "20-40", "40-60", "60-80", "80-100"]
    counts = [0] * 5
    trusted = [0] * 5
    critical = [0] * 5

    statement = select(
        McpServerRegistry.server_id,
        McpServerRegistry.trust_score,
        McpServerRegistry.risk_tier,
        McpServerRegistry.verdict,
        McpServerRegistry.registry_source,
    ).where(
        McpServerRegistry.trust_score.is_not(None),
        McpServerRegistry.trust_score >= 0,
        McpServerRegistry.trust_score <= 100,
    )

    for row in session.execute(statement):
        score = float(row.trust_score)
        if score < 20:
            index = 0
        elif score < 40:
            index = 1
        elif score < 60:
            index = 2
        elif score < 80:
            index = 3
        else:
            index = 4

        counts[index] += 1

        verdict = (row.verdict or "").strip().lower()
        if verdict == "trusted":
            trusted[index] += 1

        risk_tier = (row.risk_tier or "").strip().lower()
        if risk_tier == "critical":
            critical[index] += 1

    return TrustScoreHistogramResponse(
        bucket_size=bucket_size,
        total_scored=sum(counts),
        buckets=[
            TrustScoreBucket(
                range=ranges[index],
                count=counts[index],
                trusted=trusted[index],
                critical=critical[index],
            )
            for index in range(5)
        ],
    )


@router.get(
    "/registry/trust-score-histogram",
    response_model=TrustScoreHistogramResponse,
)
def trust_score_histogram(
    bucket: int = Query(default=5, ge=1, le=100),
    session: Session = Depends(get_session),
) -> TrustScoreHistogramResponse:
    try:
        return build_trust_score_histogram(session, bucket=bucket)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="histogram-1",
                    name="Histogram Server 1",
                    trust_score=5,
                    risk_tier="low",
                    verdict="trusted",
                    registry_source="self-test",
                ),
                McpServerRegistry(
                    server_id="histogram-2",
                    name="Histogram Server 2",
                    trust_score=45,
                    risk_tier="critical",
                    verdict="untrusted",
                    registry_source="self-test",
                ),
                McpServerRegistry(
                    server_id="histogram-3",
                    name="Histogram Server 3",
                    trust_score=75,
                    risk_tier="high",
                    verdict="untrusted",
                    registry_source="self-test",
                ),
                McpServerRegistry(
                    server_id="histogram-4",
                    name="Histogram Server 4",
                    trust_score=95,
                    risk_tier="low",
                    verdict="trusted",
                    registry_source="self-test",
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
        response = client.get(
            "/api/registry/trust-score-histogram", params={"bucket": 5}
        )
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["buckets"]) == 5
        assert payload["buckets"][2]["range"] == "40-60"
        assert payload["buckets"][2]["count"] == 1

    print("PASS")