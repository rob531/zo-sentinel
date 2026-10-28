from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import select

from app.db import get_session

try:
    from app.models import ApiKey
except ImportError:
    from app.models import ApiKey as ApiKey


BUCKET_DEFINITIONS = (
    ("0-30d", 0, 30),
    ("31-90d", 31, 90),
    ("91-180d", 91, 180),
    ("181-365d", 181, 365),
    ("365d+", 366, None),
)


class AgeBucket(BaseModel):
    bucket: str
    count: int
    label: Optional[str] = None


class ApiKeyAgeRollup(BaseModel):
    org_id: str
    bucket_days: int
    total: int
    buckets: list[AgeBucket]


router = APIRouter(prefix="/api", tags=["api-key-age-rollup"])


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported created_at value: {type(value).__name__}")

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def compute_age_buckets(rows: list[Any], bucket_days: int = 30) -> list[dict]:
    now = datetime.now(timezone.utc)
    buckets: list[dict] = [
        {"bucket": bucket_name, "count": 0, "label": None}
        for bucket_name, _, _ in BUCKET_DEFINITIONS
    ]
    newest_created_at: list[Optional[datetime]] = [None for _ in buckets]

    for row in rows:
        created_at = _row_value(row, "created_at")
        if created_at is None:
            continue

        created_at_utc = _utc_datetime(created_at)
        age_days = max(0, (now - created_at_utc).days)

        bucket_index = len(buckets) - 1
        for index, (_, lower, upper) in enumerate(BUCKET_DEFINITIONS):
            if age_days >= lower and (upper is None or age_days <= upper):
                bucket_index = index
                break

        buckets[bucket_index]["count"] += 1
        current_newest = newest_created_at[bucket_index]
        if current_newest is None or created_at_utc >= current_newest:
            newest_created_at[bucket_index] = created_at_utc
            buckets[bucket_index]["label"] = _row_value(row, "label")

    return buckets


@router.get("/keys/age-rollup", response_model=ApiKeyAgeRollup)
def get_api_key_age_rollup(
    org_id: str = Query(..., min_length=1),
    bucket_days: int = Query(30, ge=1, le=365),
    session=Depends(get_session),
) -> ApiKeyAgeRollup:
    statement = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.key_hash,
            ApiKey.label,
            ApiKey.created_at,
        )
        .where(ApiKey.org_id == org_id)
        .order_by(ApiKey.created_at.asc())
    )
    rows = list(session.execute(statement).mappings())

    return ApiKeyAgeRollup(
        org_id=org_id,
        bucket_days=bucket_days,
        total=len(rows),
        buckets=compute_age_buckets(rows, bucket_days),
    )


app = FastAPI(title="API Key Age Rollup API")
app.include_router(router)


def run() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY,
                    org_id VARCHAR(255) NOT NULL,
                    key_hash VARCHAR(255) NOT NULL,
                    label VARCHAR(255),
                    created_at DATETIME NOT NULL
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    seeded_rows = [
        (1, "org-contract", "hash-zero", "zero-day-key", now - timedelta(days=0)),
        (2, "org-contract", "hash-ten", "ten-day-key", now - timedelta(days=10)),
        (3, "org-contract", "hash-sixty", "sixty-day-key", now - timedelta(days=60)),
        (4, "org-contract", "hash-120", "one-twenty-day-key", now - timedelta(days=120)),
        (5, "org-contract", "hash-400", "four-hundred-day-key", now - timedelta(days=400)),
    ]

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, key_hash, label, created_at)
                VALUES (:id, :org_id, :key_hash, :label, :created_at)
                """
            ),
            [
                {
                    "id": row_id,
                    "org_id": org_id,
                    "key_hash": key_hash,
                    "label": label,
                    "created_at": created_at,
                }
                for row_id, org_id, key_hash, label, created_at in seeded_rows
            ],
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/keys/age-rollup",
                params={"org_id": "org-contract", "bucket_days": 30},
            )

            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["org_id"] == "org-contract"
            assert payload["bucket_days"] == 30
            assert payload["total"] == 5
            assert len(payload["buckets"]) == 5
            assert sum(bucket["count"] for bucket in payload["buckets"]) == 5

            first_bucket = payload["buckets"][0]
            assert first_bucket["bucket"] == "0-30d"
            assert first_bucket["count"] >= 1
            assert first_bucket["label"] == "zero-day-key"

            assert [bucket["bucket"] for bucket in payload["buckets"]] == [
                "0-30d",
                "31-90d",
                "91-180d",
                "181-365d",
                "365d+",
            ]
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run()