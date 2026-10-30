from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_verdict_age_buckets as _get_verdict_age_buckets


router = APIRouter(prefix="/api")


class VerdictAgeBucket(BaseModel):
    bucket: str
    count: int
    by_tier: dict[str, int]


class VerdictAgeBucketsResponse(BaseModel):
    total_servers: int
    buckets: list[VerdictAgeBucket]
    never_assessed: int
    stale_over_30d: int


@router.get(
    "/registry/verdict-age-buckets",
    response_model=VerdictAgeBucketsResponse,
)
def get_verdict_age_buckets(
    session=Depends(get_session),
    bucket_days: int = Query(default=30, ge=1, le=3650),
):
    return _get_verdict_age_buckets(session, bucket_days=bucket_days)


def _self_test() -> None:
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        McpServerRegistry(
            server_id="now",
            name="now",
            verdict="trusted",
            risk_tier="low",
            last_assessed=now,
        ),
        McpServerRegistry(
            server_id="five-days",
            name="five-days",
            verdict="conditional",
            risk_tier="medium",
            last_assessed=now - timedelta(days=5),
        ),
        McpServerRegistry(
            server_id="sixty-days",
            name="sixty-days",
            verdict="blocked",
            risk_tier="high",
            last_assessed=now - timedelta(days=60),
        ),
        McpServerRegistry(
            server_id="never",
            name="never",
            verdict="unknown",
            risk_tier=None,
            last_assessed=None,
        ),
    ]

    with Session(engine) as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/verdict-age-buckets")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        assert payload["never_assessed"] == 1
        assert payload["stale_over_30d"] == 1
        buckets = {item["bucket"]: item["count"] for item in payload["buckets"]}
        assert buckets["0-1d"] == 1
        assert buckets["30-90d"] == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()