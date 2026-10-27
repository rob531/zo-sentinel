from datetime import date, datetime, time, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

router = APIRouter(prefix="/api")
app = FastAPI(title="Vulnerability Advisory Age Bucket API")
app.include_router(router)

_BUCKET_LABELS = ("0-7", "8-30", "31-90", "91-365", "365+")
_SEVERITIES = ("critical", "high", "medium", "low")


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed
    return None


def _utc_datetime(value: Any) -> Optional[datetime]:
    parsed = _as_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_in_days(published_at: Any, now: datetime) -> Optional[int]:
    published = _utc_datetime(published_at)
    if published is None:
        return None
    elapsed = now - published
    return max(0, elapsed.days)


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 365:
        return "91-365"
    return "365+"


def get_vuln_advisory_age_buckets(
    session: Session,
    feed: Optional[str] = None,
    ecosystem: Optional[str] = None,
) -> Dict[str, Any]:
    statement = select(
        VulnAdvisory.id,
        VulnAdvisory.severity,
        VulnAdvisory.published_at,
    )
    if feed is not None:
        statement = statement.where(VulnAdvisory.feed == feed)
    if ecosystem is not None:
        statement = statement.where(VulnAdvisory.ecosystem == ecosystem)

    rows = session.execute(statement).all()
    now = datetime.now(timezone.utc)

    counts = {label: 0 for label in _BUCKET_LABELS}
    severity_mix = {
        label: {severity: 0 for severity in _SEVERITIES}
        for label in _BUCKET_LABELS
    }
    oldest_published_at: Optional[str] = None
    oldest_value: Optional[datetime] = None

    for row in rows:
        published_at = row.published_at
        published_utc = _utc_datetime(published_at)
        if published_utc is not None and (
            oldest_value is None or published_utc < oldest_value
        ):
            oldest_value = published_utc
            oldest_published_at = (
                published_at.isoformat()
                if isinstance(published_at, (datetime, date))
                else str(published_at)
            )

        age_days = _age_in_days(published_at, now)
        if age_days is None:
            continue

        bucket = _bucket_for_age(age_days)
        counts[bucket] += 1

        severity = row.severity
        if isinstance(severity, str):
            severity = severity.strip().lower()
            if severity in severity_mix[bucket]:
                severity_mix[bucket][severity] += 1

    return {
        "total": len(rows),
        "oldest_published_at": oldest_published_at,
        "buckets": [
            {
                "bucket": label,
                "count": counts[label],
                "severity_mix": severity_mix[label],
            }
            for label in _BUCKET_LABELS
        ],
    }


def get_age_buckets(
    session: Session,
    feed: Optional[str] = None,
    ecosystem: Optional[str] = None,
) -> Dict[str, Any]:
    return get_vuln_advisory_age_buckets(
        session=session,
        feed=feed,
        ecosystem=ecosystem,
    )


@router.get("/vuln/advisories/age-buckets")
def vuln_advisory_age_buckets(
    session: Session = Depends(get_session),
    feed: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    return get_vuln_advisory_age_buckets(
        session=session,
        feed=feed,
        ecosystem=ecosystem,
    )


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed VARCHAR NOT NULL,
                    severity VARCHAR NOT NULL,
                    ecosystem VARCHAR NOT NULL,
                    package VARCHAR NOT NULL,
                    published_at DATETIME NOT NULL,
                    fetched_at DATETIME NOT NULL,
                    content_hash VARCHAR NOT NULL
                )
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    test_session = TestSession()
    seed_time = datetime.now(timezone.utc)
    seed_rows = [
        (1, "osv", "critical", "npm", "advisory-one", 1),
        (2, "osv", "medium", "npm", "advisory-two", 45),
        (3, "osv", "high", "pypi", "advisory-three", 400),
    ]
    for row_id, feed_name, severity, ecosystem_name, package_name, age in seed_rows:
        test_session.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id, feed, severity, ecosystem, package,
                    published_at, fetched_at, content_hash
                ) VALUES (
                    :id, :feed, :severity, :ecosystem, :package,
                    :published_at, :fetched_at, :content_hash
                )
                """
            ),
            {
                "id": row_id,
                "feed": feed_name,
                "severity": severity,
                "ecosystem": ecosystem_name,
                "package": package_name,
                "published_at": seed_time - timedelta(days=age, seconds=1),
                "fetched_at": seed_time,
                "content_hash": f"{row_id:064x}",
            },
        )
    test_session.commit()

    def override_get_session():
        yield test_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    response = TestClient(that_app).get("/api/vuln/advisories/age-buckets")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 3, payload
    buckets = {item["bucket"]: item for item in payload["buckets"]}
    assert buckets["0-7"]["count"] == 1, payload
    assert buckets["365+"]["count"] == 1, payload
    assert all(isinstance(item["severity_mix"], dict) for item in payload["buckets"])

    test_session.close()
    test_engine.dispose()
    print("PASS")