from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey

router = APIRouter(prefix="/api")

AGE_BANDS = ("0-7", "8-30", "31-90", "91-180", "180+")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
    org_id: int,
    max_age_days: int,
    session: Session,
) -> dict:
    generated_at = datetime.now(timezone.utc)
    rows = session.execute(
        select(ApiKey).where(ApiKey.org_id == org_id).order_by(ApiKey.id)
    ).scalars().all()

    band_counts = {band: 0 for band in AGE_BANDS}
    stale_keys: List[dict] = []

    for key in rows:
        age_days = max(
            0,
            (generated_at - _as_utc(key.created_at)).days,
        )
        band_counts[_age_band(age_days)] += 1

        if age_days > max_age_days:
            stale_keys.append(
                {
                    "id": key.id,
                    "org_id": key.org_id,
                    "label": key.label,
                    "created_at": key.created_at,
                    "age_days": age_days,
                }
            )

    return {
        "generated_at": generated_at,
        "max_age_days": max_age_days,
        "total_keys": len(rows),
        "stale_count": len(stale_keys),
        "stale_keys": stale_keys,
        "bands": [
            {"band": band, "count": band_counts[band]}
            for band in AGE_BANDS
        ],
    }


@router.get("/api-keys/age-audit")
def api_key_age_audit(
    org_id: int = Query(...),
    max_age_days: int = Query(90, ge=0),
    session: Session = Depends(get_session),
) -> dict:
    return get_api_key_age_audit(org_id, max_age_days, session)


def run_self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    seeded = [
        (1, 101, "fresh", now, 10),
        (2, 101, "old", now, 100),
        (3, 202, "recent", now, 45),
        (4, 202, "ancient", now, 200),
    ]

    with TestingSessionLocal() as session:
        for key_id, org_id, label, created_at, age_days in seeded:
            session.add(
                ApiKey(
                    id=key_id,
                    org_id=org_id,
                    key_hash=f"secret-hash-{key_id}",
                    label=label,
                    created_at=created_at - __import__("datetime").timedelta(days=age_days),
                )
            )
        session.commit()

    with TestClient(that_app) as client:
        response = client.get(
            "/api/api-keys/age-audit",
            params={"org_id": 101, "max_age_days": 90},
        )

        assert response.status_code == 200, response.text
        payload = response.json()
        expected_stale = sum(
            1
            for key_id, org_id, _, _, age_days in seeded
            if org_id == 101 and age_days > 90
        )

        assert payload["stale_count"] == expected_stale
        assert payload["total_keys"] == 2
        assert sum(band["count"] for band in payload["bands"]) == payload["total_keys"]
        assert payload["max_age_days"] == 90
        assert all("key_hash" not in key for key in payload["stale_keys"])

    engine.dispose()


if __name__ == "__main__":
    run_self_test()
    print("PASS")