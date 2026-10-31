from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI
from sqlalchemy import Integer, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


def _age_days_expression(session: Session):
    created_at = McpScoreDispute.created_at
    dialect = session.get_bind().dialect.name

    if dialect == "postgresql":
        return func.floor(
            func.extract(
                "epoch",
                func.age(func.current_date() - func.date(created_at)),
            )
            / 86400
        )

    return func.cast(
        func.julianday(func.current_date())
        - func.julianday(func.date(created_at)),
        Integer,
    )


def get_backlog_age(session: Session = Depends(get_session)) -> dict:
    age_days = _age_days_expression(session).label("age_days")

    statement = (
        select(
            McpScoreDispute.reason_category,
            age_days,
        )
        .where(McpScoreDispute.status.in_(("OPEN", "IN_REVIEW")))
    )

    rows = session.execute(statement).all()

    bucket_counts = {
        "0-7d": 0,
        "8-30d": 0,
        "31-90d": 0,
        "90d+": 0,
    }
    reason_counts = {}
    oldest_open_days = 0

    for row in rows:
        age = max(0, int(row.age_days or 0))

        if age <= 7:
            bucket_counts["0-7d"] += 1
        elif age <= 30:
            bucket_counts["8-30d"] += 1
        elif age <= 90:
            bucket_counts["31-90d"] += 1
        else:
            bucket_counts["90d+"] += 1

        reason = row.reason_category or "unspecified"
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        oldest_open_days = max(oldest_open_days, age)

    return {
        "buckets": [
            {"bucket": bucket, "count": count}
            for bucket, count in bucket_counts.items()
        ],
        "total_open": len(rows),
        "oldest_open_days": oldest_open_days,
        "by_reason_category": reason_counts,
    }


get_dispute_backlog_age = get_backlog_age
compute_backlog_age = get_backlog_age


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpScoreDispute.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    testing_session.add_all(
        [
            McpScoreDispute(
                id=1,
                server_id="server-1",
                submitted_by="user-1",
                proposed_overall_risk=0.5,
                reason_category="model_quality",
                explanation="test",
                status="OPEN",
                created_at=now - timedelta(days=2),
            ),
            McpScoreDispute(
                id=2,
                server_id="server-2",
                submitted_by="user-2",
                proposed_overall_risk=0.6,
                reason_category="evidence",
                explanation="test",
                status="OPEN",
                created_at=now - timedelta(days=60),
            ),
            McpScoreDispute(
                id=3,
                server_id="server-3",
                submitted_by="user-3",
                proposed_overall_risk=0.7,
                reason_category="model_quality",
                explanation="test",
                status="RESOLVED",
                created_at=now - timedelta(days=10),
                resolved_at=now,
            ),
            McpScoreDispute(
                id=4,
                server_id="server-4",
                submitted_by="user-4",
                proposed_overall_risk=0.8,
                reason_category="evidence",
                explanation="test",
                status="OPEN",
                created_at=now - timedelta(days=200),
            ),
        ]
    )
    testing_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/disputes/backlog-age")
    def backlog_age_endpoint(payload: dict = Depends(get_backlog_age)):
        return payload

    def override_get_session():
        return testing_session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/disputes/backlog-age")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_open"] == 3
    assert next(
        item["count"]
        for item in payload["buckets"]
        if item["bucket"] == "90d+"
    ) == 1
    assert payload["oldest_open_days"] >= 200

    print("PASS")