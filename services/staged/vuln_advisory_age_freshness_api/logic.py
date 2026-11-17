from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter(prefix="/vuln/advisory-age-freshness", tags=["vuln"])

_BUCKETS = ("fresh", "recent", "aging", "stale")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_advisory_age_freshness(
    days: int = Query(default=30, ge=0, le=36500),
    session: Session = Depends(get_session),
) -> dict:
    published_times = session.execute(
        select(VulnAdvisory.published_at)
    ).scalars().all()

    now = datetime.now(timezone.utc)
    counts = {bucket: 0 for bucket in _BUCKETS}
    missing_published_at = 0

    for published_at in published_times:
        if published_at is None:
            missing_published_at += 1
            continue

        age_days = (now - _as_utc(published_at)).total_seconds() / 86400.0
        if age_days <= 7:
            counts["fresh"] += 1
        elif age_days <= 30:
            counts["recent"] += 1
        elif age_days <= 90:
            counts["aging"] += 1
        else:
            counts["stale"] += 1

    return {
        "days": days,
        "total": len(published_times),
        "buckets": [
            {"bucket": bucket, "count": counts[bucket]}
            for bucket in _BUCKETS
        ],
        "missing_published_at": missing_published_at,
    }


@router.get("")
def advisory_age_freshness(
    days: int = Query(default=30, ge=0, le=36500),
    session: Session = Depends(get_session),
) -> dict:
    return get_advisory_age_freshness(days=days, session=session)


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
    VulnAdvisory.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    fixtures = [
        (1, now),
        (2, now - timedelta(days=10)),
        (3, now - timedelta(days=45)),
        (4, now - timedelta(days=200)),
        (5, None),
    ]

    with TestSession() as session:
        for advisory_id, published_at in fixtures:
            session.add(
                VulnAdvisory(
                    id=advisory_id,
                    package=f"acceptance-package-{advisory_id}",
                    ecosystem="npm",
                    feed="acceptance-feed",
                    fetched_at=now,
                    published_at=published_at,
                    severity="medium",
                    source_url=f"https://example.invalid/advisory/{advisory_id}",
                    summary="acceptance advisory",
                    content_hash=f"acceptance-hash-{advisory_id}",
                    aliases=[],
                    identities={},
                    affected_ranges=[],
                )
            )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/vuln/advisory-age-freshness",
        params={"days": 365},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 5
    assert next(
        item["count"] for item in payload["buckets"] if item["bucket"] == "fresh"
    ) == 1
    assert payload["missing_published_at"] == 1

    print("PASS")