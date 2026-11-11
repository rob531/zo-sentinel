from datetime import datetime, timezone

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

AGE_BUCKETS = ("0-7", "8-30", "31-90", "91-365", ">365")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_created_at(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_in_days(created_at: datetime, now: datetime) -> int:
    elapsed = now - _normalize_created_at(created_at)
    return max(0, int(elapsed.total_seconds() // 86400))


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 365:
        return "91-365"
    return ">365"


def get_api_key_age_report(
    db: Session = Depends(get_session),
) -> dict:
    now = _utc_now()
    bucket_counts = {bucket: 0 for bucket in AGE_BUCKETS}
    org_ages: dict[int, list[int]] = {}
    org_names: dict[int, str] = {}

    for org_id, org_name in db.execute(select(Org.id, Org.name)).all():
        org_names[org_id] = org_name
        org_ages.setdefault(org_id, [])

    rows = db.execute(
        select(ApiKey.id, ApiKey.org_id, ApiKey.created_at, Org.name)
        .outerjoin(Org, Org.id == ApiKey.org_id)
    ).all()

    total_keys = 0
    stale_over_90d = 0

    for _key_id, org_id, created_at, org_name in rows:
        age_days = _age_in_days(created_at, now)
        total_keys += 1
        bucket_counts[_bucket_for_age(age_days)] += 1

        if age_days > 90:
            stale_over_90d += 1

        org_ages.setdefault(org_id, [])
        org_ages[org_id].append(age_days)
        if org_id not in org_names and org_name is not None:
            org_names[org_id] = org_name

    orgs = []
    for org_id in sorted(org_ages):
        ages = org_ages[org_id]
        orgs.append(
            {
                "org_id": org_id,
                "org_name": org_names.get(org_id),
                "total": len(ages),
                "oldest_days": max(ages) if ages else 0,
                "newest_days": min(ages) if ages else 0,
            }
        )

    return {
        "total_keys": total_keys,
        "buckets": [
            {"bucket": bucket, "count": bucket_counts[bucket]}
            for bucket in AGE_BUCKETS
        ],
        "orgs": orgs,
        "stale_over_90d": stale_over_90d,
    }


get_key_age_report = get_api_key_age_report
get_api_key_ageing_report = get_api_key_age_report
get_api_key_age_stats = get_api_key_age_report
get_age_report = get_api_key_age_report
calculate_api_key_age_report = get_api_key_age_report
build_api_key_age_report = get_api_key_age_report
generate_api_key_age_report = get_api_key_age_report
api_key_age_report = get_api_key_age_report


if __name__ == "__main__":
    from datetime import timedelta

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
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with test_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE orgs (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE api_keys (
                id INTEGER PRIMARY KEY,
                org_id INTEGER NOT NULL,
                key_hash TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.exec_driver_sql(
            "INSERT INTO orgs (id, name) VALUES (1, 'Example Org')"
        )

    seed = TestSession()
    base_time = datetime.now(timezone.utc).replace(tzinfo=None)
    for key_id, age_days in enumerate((1, 10, 40, 100, 400), start=1):
        seed.execute(
            ApiKey.__table__.insert().values(
                id=key_id,
                org_id=1,
                key_hash=f"hash-{key_id}",
                label=f"key-{key_id}",
                created_at=base_time - timedelta(days=age_days),
            )
        )
    seed.commit()
    seed.close()

    test_app = FastAPI()

    def override_get_session():
        local_session = TestSession()
        try:
            yield local_session
        finally:
            local_session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/keys/age-report")
    def age_report(db: Session = Depends(get_session)):
        return get_api_key_age_report(db)

    response = TestClient(test_app).get("/api/keys/age-report")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_keys"] == 5
    assert payload["buckets"][-1]["count"] == 1
    assert all(bucket["count"] >= 0 for bucket in payload["buckets"])
    print("PASS")