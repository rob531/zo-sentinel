from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


ROTATION_DAYS = 90


def _as_utc_datetime(value: Any) -> datetime:
    """Convert supported database timestamp values to an aware UTC datetime."""
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    else:
        raise TypeError(f"Unsupported timestamp value: {type(value)!r}")

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _timestamp_string(value: datetime) -> str:
    return value.isoformat()


def get_rotation_coverage(session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Build per-organisation API-key rotation inventory without exposing hashes."""
    rows = session.execute(
        select(
            ApiKey.org_id,
            ApiKey.created_at,
            Org.id,
            Org.name,
        )
        .select_from(ApiKey)
        .join(Org, ApiKey.org_id == Org.id)
    ).all()

    inventories: Dict[str, Dict[str, Any]] = {}
    now = datetime.now(timezone.utc)

    for row in rows:
        org_id = str(row.org_id)
        created_at = _as_utc_datetime(row.created_at)

        inventory = inventories.get(org_id)
        if inventory is None:
            inventory = {
                "org_id": org_id,
                "org_name": str(row.name),
                "key_count": 0,
                "oldest": created_at,
                "newest": created_at,
            }
            inventories[org_id] = inventory

        inventory["key_count"] += 1
        if created_at < inventory["oldest"]:
            inventory["oldest"] = created_at
        if created_at > inventory["newest"]:
            inventory["newest"] = created_at

    orgs: List[Dict[str, Any]] = []
    for inventory in inventories.values():
        oldest = inventory["oldest"]
        newest = inventory["newest"]
        oldest_age_days = round((now - oldest).total_seconds() / 86400.0, 2)

        orgs.append(
            {
                "org_id": inventory["org_id"],
                "org_name": inventory["org_name"],
                "key_count": int(inventory["key_count"]),
                "oldest_created_at": _timestamp_string(oldest),
                "newest_created_at": _timestamp_string(newest),
                "oldest_age_days": float(oldest_age_days),
                "stale_over_90_days": bool(oldest_age_days > ROTATION_DAYS),
            }
        )

    orgs.sort(key=lambda item: item["org_id"])

    return {
        "total_keys": sum(item["key_count"] for item in orgs),
        "org_count": len(orgs),
        "rotation_days": ROTATION_DAYS,
        "orgs": orgs,
    }


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    ApiKey.__table__.create(engine)

    TestingSession = sessionmaker(bind=engine)

    def sample_id(column: Any, index: int) -> Any:
        python_type = column.type.python_type
        if python_type is int:
            return index
        if python_type.__name__ == "UUID":
            import uuid

            return uuid.uuid4()
        return f"{column.name}-{index}"

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        org_one_id = sample_id(Org.__table__.c.id, 1)
        org_two_id = sample_id(Org.__table__.c.id, 2)

        session.add_all(
            [
                Org(id=org_one_id, name="Organisation One", created_at=now),
                Org(id=org_two_id, name="Organisation Two", created_at=now),
            ]
        )
        session.flush()

        session.add_all(
            [
                ApiKey(
                    id=sample_id(ApiKey.__table__.c.id, 1),
                    org_id=org_one_id,
                    key_hash="private-hash-one",
                    label="old key",
                    created_at=now - timedelta(days=200),
                ),
                ApiKey(
                    id=sample_id(ApiKey.__table__.c.id, 2),
                    org_id=org_one_id,
                    key_hash="private-hash-two",
                    label="recent key",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id=sample_id(ApiKey.__table__.c.id, 3),
                    org_id=org_two_id,
                    key_hash="private-hash-three",
                    label="recent key",
                    created_at=now - timedelta(days=5),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/keys/rotation")
    def rotation_route(payload: Dict[str, Any] = Depends(get_rotation_coverage)):
        return payload

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/keys/rotation")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_keys"] == 3
    assert payload["org_count"] == 2
    assert any(
        org["stale_over_90_days"] is True for org in payload["orgs"]
    )
    assert "private-hash-one" not in response.text
    assert "private-hash-two" not in response.text
    assert "private-hash-three" not in response.text

    print("PASS")


if __name__ == "__main__":
    from datetime import timedelta

    _self_test()