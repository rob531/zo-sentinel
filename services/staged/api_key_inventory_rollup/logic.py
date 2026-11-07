"""logic.py -- API key inventory computation over the real application database."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


def _as_utc(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_bucket(created_at: object, request_time: datetime) -> str:
    created = _as_utc(created_at)
    if created is None:
        return "0_7"
    age_days = max(0, (request_time - created).days)
    if age_days <= 7:
        return "0_7"
    if age_days <= 30:
        return "8_30"
    if age_days <= 90:
        return "31_90"
    return "90_plus"


def get_api_key_inventory(
    db: Session, request_time: Optional[datetime] = None
) -> Dict[str, object]:
    """Return per-organization API key counts and key-age buckets."""
    now = _as_utc(request_time) if request_time is not None else datetime.now(timezone.utc)

    rows = db.execute(
        select(
            Org.id,
            Org.name,
            Org.created_at,
            ApiKey.created_at,
        )
        .select_from(Org)
        .outerjoin(ApiKey, ApiKey.org_id == Org.id)
        .order_by(Org.id, ApiKey.created_at)
    ).all()

    grouped: Dict[object, Dict[str, object]] = {}
    for org_id, org_name, org_created_at, key_created_at in rows:
        entry = grouped.setdefault(
            org_id,
            {
                "org_id": str(org_id),
                "org_name": str(org_name),
                "key_count": 0,
                "created_at": (
                    org_created_at.isoformat()
                    if isinstance(org_created_at, datetime)
                    else None
                ),
                "age_buckets": {
                    "0_7": 0,
                    "8_30": 0,
                    "31_90": 0,
                    "90_plus": 0,
                },
            },
        )
        if key_created_at is not None:
            entry["key_count"] = int(entry["key_count"]) + 1
            buckets = entry["age_buckets"]
            buckets[_age_bucket(key_created_at, now)] += 1

    orgs = list(grouped.values())
    return {
        "orgs": orgs,
        "total_keys": sum(int(org["key_count"]) for org in orgs),
    }


build_api_key_inventory = get_api_key_inventory


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    def _id_value(column: object, number: int):
        python_type = column.type.python_type
        if python_type is int:
            return number
        if python_type is UUID:
            return uuid4()
        return str(number)

    org_one_id = _id_value(Org.__table__.c.id, 1)
    org_two_id = _id_value(Org.__table__.c.id, 2)
    org_one = Org(id=org_one_id, name="First organization")
    org_two = Org(id=org_two_id, name="Second organization")
    session.add_all([org_one, org_two])
    session.flush()

    now = datetime.utcnow()
    session.add_all(
        [
            ApiKey(
                id=_id_value(ApiKey.__table__.c.id, 1),
                key_hash="hash-one",
                label="one",
                org_id=org_one_id,
                created_at=now - timedelta(days=100),
            ),
            ApiKey(
                id=_id_value(ApiKey.__table__.c.id, 2),
                key_hash="hash-two",
                label="two",
                org_id=org_one_id,
                created_at=now - timedelta(days=2),
            ),
            ApiKey(
                id=_id_value(ApiKey.__table__.c.id, 3),
                key_hash="hash-three",
                label="three",
                org_id=org_two_id,
                created_at=now - timedelta(days=40),
            ),
        ]
    )
    session.commit()

    test_app = FastAPI()

    @test_app.get("/api/tenancy/api-key-inventory")
    def inventory(db: Session = Depends(get_session)):
        return get_api_key_inventory(db)

    test_app.dependency_overrides[get_session] = lambda: session
    with TestClient(test_app) as client:
        response = client.get("/api/tenancy/api-key-inventory")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_keys"] == 3
        first = next(org for org in payload["orgs"] if org["org_name"] == "First organization")
        assert first["key_count"] == 2
        assert first["age_buckets"]["90_plus"] == 1
        assert "key_hash" not in response.text

    session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()