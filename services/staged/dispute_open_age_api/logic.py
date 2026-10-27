from datetime import datetime, timezone
from typing import Generator

from fastapi import APIRouter, Depends, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpScoreDispute

router = APIRouter(prefix="/api", tags=["dispute-open-age"])

BUCKETS = ("0-7", "8-30", "31-90", "90+")


def _age_days(created_at: datetime, now: datetime) -> float:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)
    return max(0.0, (now - created_at).total_seconds() / 86400.0)


def _bucket_for_age(age_days: float) -> str:
    age = int(age_days)
    if age <= 7:
        return "0-7"
    if age <= 30:
        return "8-30"
    if age < 90:
        return "31-90"
    return "90+"


def get_dispute_open_age(
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
) -> dict:
    now = datetime.now(timezone.utc)
    statement = select(McpScoreDispute.created_at).where(
        McpScoreDispute.status != "resolved"
    )
    created_times = session.execute(statement).scalars().all()

    counts = {bucket: 0 for bucket in BUCKETS}
    ages = []
    for created_at in created_times:
        age = _age_days(created_at, now)
        ages.append(age)
        counts[_bucket_for_age(age)] += 1

    return {
        "days": days,
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in BUCKETS
        ],
        "oldest_open_age_days": max(ages, default=0.0),
        "total_open": len(ages),
        "generated_at": now.isoformat(),
    }


get_open_dispute_age = get_dispute_open_age
get_open_age = get_dispute_open_age


@router.get("/disputes/open-age")
def dispute_open_age(
    days: int = Query(30, ge=1),
    session: Session = Depends(get_session),
) -> dict:
    return get_dispute_open_age(days=days, session=session)


def _self_test() -> None:
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    session_factory = create_engine

    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        session.add_all(
            [
                McpScoreDispute(
                    server_id=1,
                    submitted_by="self-test",
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    explanation="Recent open dispute",
                    reason_category="test",
                    status="open",
                    admin_note=None,
                    created_at=now.replace(microsecond=0),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id=1,
                    submitted_by="self-test",
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    explanation="Recent open dispute",
                    reason_category="test",
                    status="open",
                    admin_note=None,
                    created_at=now.replace(microsecond=0),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id=1,
                    submitted_by="self-test",
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    explanation="Old open dispute",
                    reason_category="test",
                    status="open",
                    admin_note=None,
                    created_at=now.replace(microsecond=0)
                    - __import__("datetime").timedelta(days=60),
                    resolved_at=None,
                ),
                McpScoreDispute(
                    server_id=1,
                    submitted_by="self-test",
                    proposed_overall_risk=0.5,
                    proposed_axes={},
                    explanation="Resolved dispute",
                    reason_category="test",
                    status="resolved",
                    admin_note="resolved",
                    created_at=now.replace(microsecond=0)
                    - __import__("datetime").timedelta(days=60),
                    resolved_at=now.replace(microsecond=0),
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Generator[Session, None, None]:
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/open-age?days=30")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_open"] == 3
    assert next(
        bucket["count"]
        for bucket in payload["buckets"]
        if bucket["bucket"] == "31-90"
    ) == 1
    print("PASS")


if __name__ == "__main__":
    _self_test()