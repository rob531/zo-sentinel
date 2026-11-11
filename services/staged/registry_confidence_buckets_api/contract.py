from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ConfidenceBucket(BaseModel):
    bucket: str
    count: int
    avg_trust_score: float


class ConfidenceBucketsResponse(BaseModel):
    buckets: list[ConfidenceBucket]
    total: int
    low_confidence_count: int
    as_of: str


router = APIRouter(prefix="/api")


def _bucket_stats(session: Session, bucket: str) -> ConfidenceBucket:
    count = 0
    avg_trust_score = 0.0

    if bucket == "unknown":
        query = select(
            func.count(),
            func.coalesce(func.avg(McpServerRegistry.trust_score), 0.0),
        ).where(McpServerRegistry.confidence.is_(None))
    else:
        bounds = {
            "0.0-0.25": (0.0, 0.25),
            "0.25-0.5": (0.25, 0.5),
            "0.5-0.75": (0.5, 0.75),
            "0.75-1.0": (0.75, 1.0),
        }
        lower, upper = bounds[bucket]
        conditions = [
            McpServerRegistry.confidence.is_not(None),
            McpServerRegistry.confidence >= lower,
        ]
        if bucket == "0.75-1.0":
            conditions.append(McpServerRegistry.confidence <= upper)
        else:
            conditions.append(McpServerRegistry.confidence < upper)

        query = select(
            func.count(),
            func.coalesce(func.avg(McpServerRegistry.trust_score), 0.0),
        ).where(*conditions)

    count, avg_trust_score = session.execute(query).one()
    return ConfidenceBucket(
        bucket=bucket,
        count=int(count or 0),
        avg_trust_score=float(avg_trust_score or 0.0),
    )


@router.get(
    "/registry/confidence-buckets",
    response_model=ConfidenceBucketsResponse,
)
def get_confidence_buckets(
    session: Session = Depends(get_session),
) -> ConfidenceBucketsResponse:
    bucket_names = (
        "0.0-0.25",
        "0.25-0.5",
        "0.5-0.75",
        "0.75-1.0",
        "unknown",
    )
    buckets = [_bucket_stats(session, bucket) for bucket in bucket_names]
    total = int(
        session.execute(
            select(func.count()).select_from(McpServerRegistry)
        ).scalar_one()
    )
    low_confidence_count = buckets[0].count + buckets[4].count

    return ConfidenceBucketsResponse(
        buckets=buckets,
        total=total,
        low_confidence_count=low_confidence_count,
        as_of=datetime.now(timezone.utc).isoformat(),
    )


app = FastAPI(title="Registry Confidence Buckets API")
app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seed_rows = [
        ("confidence-001", 0.10, 0.40),
        ("confidence-002", 0.40, 0.70),
        ("confidence-003", 0.90, 0.95),
        ("confidence-004", None, 0.20),
    ]

    with session_factory() as session:
        for server_id, confidence, trust_score in seed_rows:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=f"Test Server {server_id}",
                    url=f"https://{server_id}.example.test",
                    registry_source="contract-self-test",
                    risk_tier="low",
                    verdict="approved",
                    confidence=confidence,
                    trust_score=trust_score,
                    last_assessed=now,
                )
            )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session() -> Session:
        return session_factory()

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/confidence-buckets")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 4
            unknown = next(
                bucket for bucket in payload["buckets"] if bucket["bucket"] == "unknown"
            )
            assert unknown["count"] == 1
            assert sum(bucket["count"] for bucket in payload["buckets"]) == payload["total"]
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()