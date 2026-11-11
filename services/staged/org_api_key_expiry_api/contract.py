from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey, Org


class ExpiryKey(BaseModel):
    id: Any
    label: Optional[str]
    age_days: int


class ExpiryBucket(BaseModel):
    bucket: str
    count: int
    keys: List[ExpiryKey]


class OrgApiKeyExpiryResponse(BaseModel):
    org_id: Any
    org_name: str
    total_keys: int
    buckets: List[ExpiryBucket]


BUCKET_ORDER = ("fresh_30d", "aging_90d", "stale_over_90d")


def _bucket_for_age(age_days: int) -> str:
    if age_days <= 30:
        return "fresh_30d"
    if age_days <= 90:
        return "aging_90d"
    return "stale_over_90d"


def _utc_date(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


router = APIRouter(prefix="/api")


@router.get(
    "/orgs/{org_id}/api-keys/expiry",
    response_model=OrgApiKeyExpiryResponse,
)
def get_org_api_key_expiry(
    org_id: str,
    session: Session = Depends(get_session),
) -> OrgApiKeyExpiryResponse:
    org = session.execute(
        select(Org.id, Org.name).where(Org.id == org_id)
    ).one_or_none()
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    rows = session.execute(
        select(
            ApiKey.id,
            ApiKey.label,
            ApiKey.created_at,
        )
        .join(Org, ApiKey.org_id == Org.id)
        .where(ApiKey.org_id == org_id)
        .order_by(ApiKey.created_at, ApiKey.id)
    ).all()

    grouped: Dict[str, List[ExpiryKey]] = {bucket: [] for bucket in BUCKET_ORDER}
    today = datetime.now(timezone.utc).date()

    for key_id, label, created_at in rows:
        age_days = (today - _utc_date(created_at).date()).days
        bucket = _bucket_for_age(age_days)
        grouped[bucket].append(
            ExpiryKey(id=key_id, label=label, age_days=age_days)
        )

    buckets = [
        ExpiryBucket(bucket=bucket, count=len(grouped[bucket]), keys=grouped[bucket])
        for bucket in BUCKET_ORDER
    ]

    return OrgApiKeyExpiryResponse(
        org_id=org.id,
        org_name=org.name,
        total_keys=len(rows),
        buckets=buckets,
    )


app = FastAPI()
app.include_router(router)


def _test_id(table: Any, prefix: str, number: int) -> Any:
    try:
        python_type = table.c.id.type.python_type
    except (AttributeError, NotImplementedError):
        python_type = str

    if python_type is int:
        return number
    if python_type is uuid.UUID:
        return str(uuid.uuid4())
    return f"{prefix}-{number}"


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    now = datetime.now(timezone.utc)
    org_one_id = _test_id(Org.__table__, "org", 1)
    org_two_id = _test_id(Org.__table__, "org", 2)

    with SessionLocal() as session:
        session.execute(
            insert(Org.__table__),
            [
                {
                    "id": org_one_id,
                    "name": "Contract Org One",
                    "created_at": now,
                },
                {
                    "id": org_two_id,
                    "name": "Contract Org Two",
                    "created_at": now,
                },
            ],
        )

        session.execute(
            insert(ApiKey.__table__),
            [
                {
                    "id": _test_id(ApiKey.__table__, "key", 1),
                    "org_id": org_one_id,
                    "key_hash": "contract-secret-hash-5-days",
                    "label": "five-day-key",
                    "created_at": now.replace(microsecond=0) - _days(5),
                },
                {
                    "id": _test_id(ApiKey.__table__, "key", 2),
                    "org_id": org_one_id,
                    "key_hash": "contract-secret-hash-60-days",
                    "label": "sixty-day-key",
                    "created_at": now.replace(microsecond=0) - _days(60),
                },
                {
                    "id": _test_id(ApiKey.__table__, "key", 3),
                    "org_id": org_one_id,
                    "key_hash": "contract-secret-hash-200-days",
                    "label": "two-hundred-day-key",
                    "created_at": now.replace(microsecond=0) - _days(200),
                },
            ],
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    self_app = FastAPI()
    self_app.include_router(router)
    self_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    with TestClient(self_app) as client:
        response = client.get(f"/api/orgs/{org_one_id}/api-keys/expiry")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_keys"] == 3, body
    assert body["org_id"] == org_one_id, body
    assert body["org_name"] == "Contract Org One", body

    buckets = {bucket["bucket"]: bucket for bucket in body["buckets"]}
    assert set(buckets) == set(BUCKET_ORDER), body
    assert sum(bucket["count"] for bucket in buckets.values()) == 3, body

    returned_keys = []
    for bucket_name, bucket in buckets.items():
        assert bucket["count"] == len(bucket["keys"]), body
        for key in bucket["keys"]:
            returned_keys.append(key)
            assert _bucket_for_age(key["age_days"]) == bucket_name, body

    assert len(returned_keys) == 3, body
    assert {key["age_days"] for key in returned_keys} == {5, 60, 200}, body
    assert {buckets[_bucket_for_age(key["age_days"])]["count"] for key in returned_keys} == {1}, body
    assert "key_hash" not in response.text, response.text
    assert "contract-secret-hash" not in response.text, response.text

    print("PASS")


def _days(days: int):
    from datetime import timedelta

    return timedelta(days=days)


if __name__ == "__main__":
    _run_self_test()