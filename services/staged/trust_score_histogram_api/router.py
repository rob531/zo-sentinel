from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_trust_score_histogram

router = APIRouter()


class TrustScoreBucket(BaseModel):
    label: str
    count: int
    min: float
    max: float


class TrustScoreHistogramResponse(BaseModel):
    total: int
    bucketed: int
    null_count: int
    buckets: list[TrustScoreBucket]


@router.get(
    "/registry/trust-score-histogram",
    response_model=TrustScoreHistogramResponse,
)
def trust_score_histogram(
    session: Session = Depends(get_session),
) -> TrustScoreHistogramResponse:
    return get_trust_score_histogram(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from starlette.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    scores = [0.0, 55.0, 95.0] + [25.0] * 21 + [None]
    assessed_at = datetime.now(timezone.utc)

    with TestSession() as session:
        for index, score in enumerate(scores):
            session.add(
                McpServerRegistry(
                    server_id=f"histogram-test-{index}",
                    trust_score=score,
                    verdict="allow",
                    risk_tier="low",
                    registry_source="self-test",
                    last_assessed=assessed_at,
                )
            )
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/registry/trust-score-histogram")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 25
    assert payload["bucketed"] == 24
    assert payload["null_count"] == 1
    assert next(bucket for bucket in payload["buckets"] if bucket["label"] == "50-60")[
        "count"
    ] == 1

    print("PASS")