from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey


AGE_BANDS = ("0-7", "8-30", "31-90", "91-180", "180+")
_SECONDS_PER_DAY = 86400


def _utc_datetime(value: datetime | str) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _age_days(created_at: datetime | str, now: datetime) -> int:
    elapsed = (now - _utc_datetime(created_at)).total_seconds()
    return max(0, int(elapsed // _SECONDS_PER_DAY))


def _age_band(age_days: int) -> str:
    if age_days <= 7:
        return "0-7"
    if age_days <= 30:
        return "8-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    return "180+"


def get_api_key_age_audit(
    session: Session,
    org_id: int | None = None,
    max_age_days: int = 90,
) -> dict[str, Any]:
    if max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")

    statement = select(
        ApiKey.id,
        ApiKey.org_id,
        ApiKey.label,
        ApiKey.created_at,
    )
    if org_id is not None:
        statement = statement.where(ApiKey.org_id == org_id)

    now = datetime.now(timezone.utc)
    rows = session.execute(statement).all()

    band_counts = {band: 0 for band in AGE_BANDS}
    stale_keys: list[dict[str, Any]] = []

    for key_id, key_org_id, label, created_at in rows:
        age_days = _age_days(created_at, now)
        band_counts[_age_band(age_days)] += 1

        if age_days > max_age_days:
            stale_keys.append(
                {
                    "id": key_id,
                    "org_id": key_org_id,
                    "label": label,
                    "created_at": created_at.isoformat()
                    if isinstance(created_at, datetime)
                    else created_at,
                    "age_days": age_days,
                }
            )

    stale_keys.sort(key=lambda item: (item["age_days"], item["id"]))

    return {
        "generated_at": now.isoformat(),
        "max_age_days": max_age_days,
        "total_keys": len(rows),
        "stale_count": len(stale_keys),
        "stale_keys": stale_keys,
        "bands": [
            {"band": band, "count": band_counts[band]}
            for band in AGE_BANDS
        ],
    }


if __name__ == "__main__":
    from datetime import timedelta

    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    seeded_at = datetime.now(timezone.utc)
    test_session.add_all(
        [
            ApiKey(
                id=1,
                org_id=10,
                key_hash="seed-hash-1",
                label="fresh-key",
                created_at=seeded_at - timedelta(days=5),
            ),
            ApiKey(
                id=2,
                org_id=10,
                key_hash="seed-hash-2",
                label="recent-key",
                created_at=seeded_at - timedelta(days=20),
            ),
            ApiKey(
                id=3,
                org_id=20,
                key_hash="seed-hash-3",
                label="stale-key",
                created_at=seeded_at - timedelta(days=100),
            ),
            ApiKey(
                id=4,
                org_id=20,
                key_hash="seed-hash-4",
                label="ancient-key",
                created_at=seeded_at - timedelta(days=200),
            ),
        ]
    )
    test_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/api-keys/age-audit")
    def api_key_age_audit(
        org_id: int | None = None,
        max_age_days: int = 90,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_api_key_age_audit(session, org_id, max_age_days)

    def override_get_session():
        yield test_session

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get(
        "/api/api-keys/age-audit",
        params={"max_age_days": 90},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_keys"] == 4
    assert payload["stale_count"] == 2
    assert sum(band["count"] for band in payload["bands"]) == payload["total_keys"]
    assert all("key_hash" not in key for key in payload["stale_keys"])

    filtered_response = client.get(
        "/api/api-keys/age-audit",
        params={"org_id": 10, "max_age_days": 90},
    )
    assert filtered_response.status_code == 200
    assert filtered_response.json()["total_keys"] == 2

    print("PASS")