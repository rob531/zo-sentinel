from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org


AGE_BANDS = ("0-30", "31-90", "91-180", "181-365", "365+")


def _age_days(created_at: datetime, now: datetime) -> int:
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    else:
        created_at = created_at.astimezone(timezone.utc)

    elapsed_seconds = max(0.0, (now - created_at).total_seconds())
    return int(elapsed_seconds // 86400)


def _age_band(age_days: int) -> str:
    if age_days <= 30:
        return "0-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    if age_days <= 365:
        return "181-365"
    return "365+"


def get_api_key_rotation_audit(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    rows = session.execute(
        select(Org.id, Org.name, ApiKey.id, ApiKey.created_at)
        .outerjoin(ApiKey, ApiKey.org_id == Org.id)
    ).all()

    band_counts = {band: 0 for band in AGE_BANDS}
    org_metrics: dict[str, dict[str, Any]] = {}

    for org_id, org_name, api_key_id, created_at in rows:
        org_key = str(org_id)
        if org_key not in org_metrics:
            org_metrics[org_key] = {
                "org_id": org_key,
                "org_name": str(org_name),
                "total_keys": 0,
                "rotation_due": 0,
                "oldest_key_age_days": 0,
            }

        if api_key_id is None:
            continue

        age_days = (
            0
            if created_at is None
            else _age_days(created_at, now)
        )
        band = _age_band(age_days)
        band_counts[band] += 1

        metrics = org_metrics[org_key]
        metrics["total_keys"] += 1
        if age_days > 90:
            metrics["rotation_due"] += 1
        metrics["oldest_key_age_days"] = max(
            metrics["oldest_key_age_days"], age_days
        )

    orgs = sorted(
        org_metrics.values(),
        key=lambda item: (item["org_name"].lower(), item["org_id"]),
    )
    total_keys = sum(item["total_keys"] for item in orgs)
    rotation_due_total = sum(item["rotation_due"] for item in orgs)

    return {
        "age_bands": [
            {"age_band": band, "count": band_counts[band]}
            for band in AGE_BANDS
            if band_counts[band] > 0
        ],
        "orgs": orgs,
        "total_keys": total_keys,
        "rotation_due_total": rotation_due_total,
        "generated_at": now.isoformat(),
    }


get_rotation_audit = get_api_key_rotation_audit
get_api_key_rotation_data = get_api_key_rotation_audit
get_api_key_audit = get_api_key_rotation_audit
audit_api_key_rotation = get_api_key_rotation_audit


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
    TestSession = sessionmaker(
        bind=test_engine,
        autocommit=False,
        autoflush=False,
    )

    Org.__table__.create(test_engine)
    ApiKey.__table__.create(test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as session:
        session.add_all(
            [
                Org(id="org-1", name="Example Org", created_at=now),
                Org(id="org-2", name="Second Org", created_at=now),
                ApiKey(
                    id="key-1",
                    org_id="org-1",
                    label="old-key",
                    key_hash="hash-1",
                    created_at=now - timedelta(days=200),
                ),
                ApiKey(
                    id="key-2",
                    org_id="org-1",
                    label="new-key",
                    key_hash="hash-2",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-3",
                    org_id="org-2",
                    label="medium-key",
                    key_hash="hash-3",
                    created_at=now - timedelta(days=60),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/audit/api-keys/rotation")
    def rotation_audit(session: Session = Depends(get_session)):
        return get_api_key_rotation_audit(session)

    response = TestClient(test_app).get("/api/audit/api-keys/rotation")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_keys"] == 3
    assert payload["rotation_due_total"] == 1
    assert len(payload["age_bands"]) == 3
    assert "key_hash" not in response.text

    print("PASS")