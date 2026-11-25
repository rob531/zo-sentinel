"""logic.py -- read-only API key rotation watch over the real application data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session  # noqa: F401
from app.models import ApiKey, Org


_BANDS = ("0-30", "31-90", "91-180", "181-365", "365+")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(created_at: datetime, now: datetime) -> int:
    return max(0, int((now - _utc(created_at)).total_seconds() // 86400))


def _band(age_days: int) -> str:
    if age_days <= 30:
        return "0-30"
    if age_days <= 90:
        return "31-90"
    if age_days <= 180:
        return "91-180"
    if age_days <= 365:
        return "181-365"
    return "365+"


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _utc(value).isoformat()


def get_rotation_watch(
    db: Session,
    org_id: Optional[str] = None,
    min_age_days: int = 90,
    limit: int = 200,
) -> Dict[str, Any]:
    """Return stale API keys and age-band counts grouped by organization.

    ``org_id`` is optional; an empty value means all organizations. The
    ``limit`` controls the number of key details returned, while aggregate
    totals and band counts describe all matching keys.
    """
    now = datetime.now(timezone.utc)
    statement = (
        select(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.label,
            ApiKey.created_at,
            Org.name,
        )
        .select_from(ApiKey)
        .outerjoin(Org, ApiKey.org_id == Org.id)
        .order_by(ApiKey.created_at.asc(), ApiKey.id.asc())
    )
    if org_id and org_id.strip():
        statement = statement.where(ApiKey.org_id == org_id)

    rows = db.execute(statement).all()

    band_counts = {band: 0 for band in _BANDS}
    organizations: Dict[str, Dict[str, Any]] = {}
    total_keys = 0
    stale_keys = 0

    for key_id, key_org_id, label, created_at, org_name in rows:
        age = _age_days(created_at, now)
        band = _band(age)
        band_counts[band] += 1
        total_keys += 1
        if age >= min_age_days:
            stale_keys += 1

        org_key = str(key_org_id)
        org = organizations.setdefault(
            org_key,
            {
                "org_id": org_key,
                "org_name": org_name,
                "total_keys": 0,
                "stale_keys": 0,
                "oldest_created_at": None,
                "keys": [],
            },
        )
        org["total_keys"] += 1
        if age >= min_age_days:
            org["stale_keys"] += 1
        oldest = org["oldest_created_at"]
        if oldest is None or _utc(created_at) < _utc(oldest):
            org["oldest_created_at"] = created_at

    remaining = max(0, int(limit))
    for org in organizations.values():
        for key_id, key_org_id, label, created_at, org_name in rows:
            if remaining <= 0:
                break
            if str(key_org_id) != org["org_id"]:
                continue
            org["keys"].append(
                {
                    "key_id": str(key_id),
                    "label": label,
                    "age_days": _age_days(created_at, now),
                }
            )
            remaining -= 1
        if remaining <= 0:
            break

    return {
        "total_keys": total_keys,
        "stale_keys": stale_keys,
        "bands": [{"band": band, "count": band_counts[band]} for band in _BANDS],
        "orgs": list(organizations.values()),
    }


def rotation_watch(
    db: Session,
    org_id: Optional[str] = None,
    min_age_days: int = 90,
    limit: int = 200,
) -> Dict[str, Any]:
    """Compatibility name for the rotation-watch computation."""
    return get_rotation_watch(db, org_id, min_age_days, limit)


def build_rotation_watch(
    db: Session,
    org_id: Optional[str] = None,
    min_age_days: int = 90,
    limit: int = 200,
) -> Dict[str, Any]:
    """Compatibility name for the rotation-watch computation."""
    return get_rotation_watch(db, org_id, min_age_days, limit)


def __main__() -> None:
    from datetime import timedelta

    from fastapi import FastAPI, Query
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
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                Org(id="org-001", name="Example Org", created_at=now),
                Org(id="org-002", name="Second Org", created_at=now),
                Org(id="org-003", name="Third Org", created_at=now),
                ApiKey(
                    id="key-001",
                    org_id="org-001",
                    key_hash="literal-secret-one",
                    label="old key",
                    created_at=now - timedelta(days=400),
                ),
                ApiKey(
                    id="key-002",
                    org_id="org-001",
                    key_hash="literal-secret-two",
                    label="new key",
                    created_at=now - timedelta(days=10),
                ),
                ApiKey(
                    id="key-003",
                    org_id="org-002",
                    key_hash="literal-secret-three",
                    label="middle key",
                    created_at=now - timedelta(days=120),
                ),
                ApiKey(
                    id="key-004",
                    org_id="org-003",
                    key_hash="literal-secret-four",
                    label="recent key",
                    created_at=now - timedelta(days=60),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    def session_override():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = session_override

    @test_app.get("/api/keys/rotation-watch")
    def rotation_watch_endpoint(
        org_id: Optional[str] = Query(default=None),
        min_age_days: int = Query(default=90, ge=0),
        limit: int = Query(default=200, ge=1),
    ):
        with SessionLocal() as session:
            return get_rotation_watch(session, org_id, min_age_days, limit)

    response = TestClient(test_app).get(
        "/api/keys/rotation-watch", params={"min_age_days": 90}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_keys"] == 4
    assert body["stale_keys"] >= 1
    assert len(body["bands"]) == 5
    assert all("literal-secret" not in str(value) for value in body.values())
    print("PASS")


if __name__ == "__main__":
    __main__()