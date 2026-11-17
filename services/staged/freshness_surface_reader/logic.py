from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


router = APIRouter(prefix="/api")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_sla_seconds(payload: Any) -> int:
    if isinstance(payload, dict):
        try:
            return int(payload.get("sla_seconds", 3600))
        except (TypeError, ValueError):
            return 3600
    return 3600


def read_freshness_surfaces(
    db: Session, stale_only: bool = False
) -> Dict[str, Any]:
    """Return the latest persisted freshness measurement for each surface."""
    now = _utc_now()
    rows = db.execute(
        select(FreshnessStat).order_by(FreshnessStat.computed_at.desc())
    ).scalars().all()

    latest: Dict[str, Any] = {}
    for row in rows:
        if row.surface in latest:
            continue

        computed_at = _as_utc(row.computed_at)
        age_seconds = max(0, int((now - computed_at).total_seconds()))
        payload = row.payload if isinstance(row.payload, dict) else {}
        sla_seconds = _payload_sla_seconds(row.payload)

        if stale_only and age_seconds <= sla_seconds:
            continue

        latest[row.surface] = {
            "surface": row.surface,
            "computed_at": computed_at.isoformat(),
            "compute_seconds": (
                int(row.compute_seconds)
                if row.compute_seconds is not None
                else None
            ),
            "age_seconds": age_seconds,
            "payload": payload,
        }

    return {
        "generated_at": now.isoformat(),
        "surfaces": list(latest.values()),
    }


@router.get("/freshness/surfaces")
def freshness_surfaces(
    stale_only: bool = Query(False),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return read_freshness_surfaces(db, stale_only=stale_only)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc)
    with TestingSession() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="surface_fresh_a",
                    computed_at=now,
                    compute_seconds=1,
                    payload={"sla_seconds": 3600},
                ),
                FreshnessStat(
                    surface="surface_fresh_b",
                    computed_at=now,
                    compute_seconds=2,
                    payload={"sla_seconds": 3600},
                ),
                FreshnessStat(
                    surface="surface_stale",
                    computed_at=now.replace(microsecond=0)
                    - __import__("datetime").timedelta(seconds=7200),
                    compute_seconds=3,
                    payload={"sla_seconds": 3600},
                ),
            ]
        )
        session.commit()

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["surfaces"]) == 3, body
        assert all(
            isinstance(surface["age_seconds"], int)
            and surface["age_seconds"] >= 0
            for surface in body["surfaces"]
        ), body

        stale_response = client.get(
            "/api/freshness/surfaces", params={"stale_only": "true"}
        )
        assert stale_response.status_code == 200, stale_response.text
        stale_body = stale_response.json()
        assert len(stale_body["surfaces"]) == 1, stale_body
        assert stale_body["surfaces"][0]["surface"] == "surface_stale", stale_body

    print("PASS")