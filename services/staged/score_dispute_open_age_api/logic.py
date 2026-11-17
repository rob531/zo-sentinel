from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import Integer, and_, case, cast, func, insert, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpScoreDispute


_BUCKET_LABELS = ("0-2", "3-7", "8-14", "15-30", "30+")


def _as_of() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _age_expression(session: Session, as_of: datetime):
    """Return an age-in-days expression calculated by the database."""
    dialect = session.bind.dialect.name if session.bind is not None else "postgresql"

    if dialect == "sqlite":
        age_fraction = (
            func.julianday(as_of) - func.julianday(McpScoreDispute.created_at)
        ) / 1.0
    else:
        age_fraction = (
            func.extract(
                "epoch",
                as_of - McpScoreDispute.created_at,
            )
            / 86400.0
        )

    age_days = cast(func.floor(age_fraction), Integer)
    return case((age_days < 0, 0), else_=age_days)


def _iso_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def get_dispute_open_age(session: Session = Depends(get_session)) -> Dict[str, Any]:
    as_of = _as_of()
    age_days = _age_expression(session, as_of)

    open_condition = and_(
        McpScoreDispute.status != "resolved",
        McpScoreDispute.resolved_at.is_(None),
    )

    aggregate = select(
        func.count(McpScoreDispute.id).label("open_count"),
        func.avg(age_days).label("mean_age_days"),
        func.min(McpScoreDispute.created_at).label("oldest_created_at"),
        func.sum(case((age_days <= 2, 1), else_=0)).label("bucket_0_2"),
        func.sum(case((age_days.between(3, 7), 1), else_=0)).label("bucket_3_7"),
        func.sum(case((age_days.between(8, 14), 1), else_=0)).label("bucket_8_14"),
        func.sum(case((age_days.between(15, 30), 1), else_=0)).label("bucket_15_30"),
        func.sum(case((age_days > 30, 1), else_=0)).label("bucket_30_plus"),
    ).where(open_condition)

    result = session.execute(aggregate).one()
    counts = [
        int(result.bucket_0_2 or 0),
        int(result.bucket_3_7 or 0),
        int(result.bucket_8_14 or 0),
        int(result.bucket_15_30 or 0),
        int(result.bucket_30_plus or 0),
    ]

    mean_age = result.mean_age_days
    if mean_age is not None:
        mean_age = round(float(mean_age), 2)
    else:
        mean_age = 0.0

    return {
        "open_count": int(result.open_count or 0),
        "buckets": [
            {"label": label, "count": count}
            for label, count in zip(_BUCKET_LABELS, counts)
        ],
        "oldest_created_at": _iso_timestamp(result.oldest_created_at),
        "mean_age_days": mean_age,
    }


# Compatibility names used by routers and service consumers.
get_open_age = get_dispute_open_age
get_open_age_breakdown = get_dispute_open_age
get_dispute_open_age_breakdown = get_dispute_open_age
get_open_dispute_age = get_dispute_open_age


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
    McpScoreDispute.__table__.create(test_engine)
    TestingSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestingSession() as session:
        session.execute(
            insert(McpScoreDispute).values(
                server_id="test-server",
                submitted_by="tester",
                proposed_overall_risk="low",
                proposed_axes={},
                reason_category="test",
                status="open",
                admin_note=None,
                explanation="test dispute",
                created_at=now.replace() - __import__("datetime").timedelta(days=1),
                resolved_at=None,
            )
        )
        session.execute(
            insert(McpScoreDispute).values(
                server_id="test-server",
                submitted_by="tester",
                proposed_overall_risk="low",
                proposed_axes={},
                reason_category="test",
                status="open",
                admin_note=None,
                explanation="test dispute",
                created_at=now.replace() - __import__("datetime").timedelta(days=5),
                resolved_at=None,
            )
        )
        session.execute(
            insert(McpScoreDispute).values(
                server_id="test-server",
                submitted_by="tester",
                proposed_overall_risk="low",
                proposed_axes={},
                reason_category="test",
                status="open",
                admin_note=None,
                explanation="test dispute",
                created_at=now.replace() - __import__("datetime").timedelta(days=40),
                resolved_at=None,
            )
        )
        session.execute(
            insert(McpScoreDispute).values(
                server_id="test-server",
                submitted_by="tester",
                proposed_overall_risk="low",
                proposed_axes={},
                reason_category="test",
                status="resolved",
                admin_note="done",
                explanation="resolved dispute",
                created_at=now.replace() - __import__("datetime").timedelta(days=60),
                resolved_at=now.replace(),
            )
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.get("/api/disputes/open-age")(get_dispute_open_age)

    response = TestClient(that_app).get("/api/disputes/open-age")
    assert response.status_code == 200
    payload = response.json()
    assert payload["open_count"] == 3
    assert next(bucket for bucket in payload["buckets"] if bucket["label"] == "30+")[
        "count"
    ] == 1
    assert all(bucket["count"] != 4 for bucket in payload["buckets"])
    print("PASS")