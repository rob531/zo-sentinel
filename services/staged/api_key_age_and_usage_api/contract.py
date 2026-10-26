from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from app.db import get_session
from app.models import Org

try:
    from app.models import ApiKey
except ImportError:
    ApiKey = None


class BucketCount(BaseModel):
    bucket: str
    count: int


class OrgKeyCount(BaseModel):
    org_id: int
    org_name: Optional[str]
    key_count: int


class KeyAgeProfile(BaseModel):
    generated_at: datetime
    total_keys: int
    distinct_orgs: int
    buckets: List[BucketCount]
    per_org: List[OrgKeyCount]


def _parse_created_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _age_days(created_at: Any, now: datetime) -> Optional[int]:
    parsed = _parse_created_at(created_at)
    if parsed is None:
        return None
    return max(0, (now - parsed).days)


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


def get_key_age_profile(session: Any) -> KeyAgeProfile:
    now = datetime.now(timezone.utc)

    with session.connection() as connection:
        key_rows = connection.exec_driver_sql(
            """
            SELECT id, org_id, created_at
            FROM api_keys
            ORDER BY id
            """
        ).mappings().all()

        org_rows = connection.exec_driver_sql(
            """
            SELECT id, name
            FROM orgs
            ORDER BY id
            """
        ).mappings().all()

    bucket_names = ["0-7", "8-30", "31-90", "91-365", "365+"]
    bucket_counts = {name: 0 for name in bucket_names}
    org_key_counts: Dict[int, int] = {}
    distinct_org_ids = set()

    for row in key_rows:
        org_id = int(row["org_id"])
        distinct_org_ids.add(org_id)
        org_key_counts[org_id] = org_key_counts.get(org_id, 0) + 1

        age_days = _age_days(row["created_at"], now)
        if age_days is None:
            age_days = 0
        bucket_counts[_bucket_for_age(age_days)] += 1

    per_org = []
    for row in org_rows:
        org_id = int(row["id"])
        per_org.append(
            OrgKeyCount(
                org_id=org_id,
                org_name=row["name"],
                key_count=org_key_counts.get(org_id, 0),
            )
        )

    known_org_ids = {int(row["id"]) for row in org_rows}
    for org_id in sorted(distinct_org_ids - known_org_ids):
        per_org.append(
            OrgKeyCount(
                org_id=org_id,
                org_name=None,
                key_count=org_key_counts.get(org_id, 0),
            )
        )

    return KeyAgeProfile(
        generated_at=now,
        total_keys=len(key_rows),
        distinct_orgs=len(distinct_org_ids),
        buckets=[
            BucketCount(bucket=name, count=bucket_counts[name])
            for name in bucket_names
        ],
        per_org=per_org,
    )


router = APIRouter(prefix="/api")


@router.get("/keys/age-profile", response_model=KeyAgeProfile)
def key_age_profile(session: Any = Depends(get_session)) -> KeyAgeProfile:
    return get_key_age_profile(session)


app = FastAPI()
app.include_router(router)


def run_contract_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE orgs (
                    id INTEGER PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER NOT NULL,
                    key_hash VARCHAR(255) NOT NULL,
                    label VARCHAR(255),
                    created_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO orgs (id, name, created_at)
                VALUES
                    (1, 'Acme', '2024-01-01T00:00:00Z'),
                    (2, 'Example Corp', '2024-01-01T00:00:00Z')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, key_hash, label, created_at)
                VALUES
                    (1, 1, 'hash-1', 'primary', '2024-01-01T00:00:00Z'),
                    (2, 1, 'hash-2', 'secondary', '2024-02-01T00:00:00Z'),
                    (3, 2, 'hash-3', 'example', '2024-04-01T00:00:00Z')
                """
            )
        )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get("/api/keys/age-profile")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_keys"] == 3
            assert sum(item["count"] for item in payload["buckets"]) == payload["total_keys"]
            assert payload["distinct_orgs"] == 2
    finally:
        app.dependency_overrides.clear()
        test_engine.dispose()


if __name__ == "__main__":
    run_contract_test()
    print("PASS")