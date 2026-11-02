"""Freshness SLA rollup logic over the real application database."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


router = APIRouter(prefix="/api")
api_router = router


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return _as_utc(value).isoformat()


def freshness_sla_rollup(
    db: Session,
    surface: Optional[str] = None,
    max_age_seconds: int = 3600,
) -> Dict[str, Any]:
    """Return per-surface freshness status and aggregate counts."""
    if not 1 <= max_age_seconds <= 86400:
        raise ValueError("max_age_seconds must be between 1 and 86400")

    query = select(FreshnessStat)
    if surface is not None:
        query = query.where(FreshnessStat.surface == surface)
    query = query.order_by(FreshnessStat.surface)

    rows = db.execute(query).scalars().all()
    now = _utc_now()
    surfaces: list[Dict[str, Any]] = []
    counts = {"fresh": 0, "stale": 0, "missing": 0}

    for row in rows:
        computed_at = row.computed_at
        if computed_at is None:
            age_seconds: Optional[int] = None
            status = "missing"
            computed_at_value: Optional[str] = None
        else:
            age = max(0.0, (now - _as_utc(computed_at)).total_seconds())
            age_seconds = int(age)
            status = "fresh" if age_seconds <= max_age_seconds else "stale"
            computed_at_value = _isoformat(computed_at)

        counts[status] += 1
        surfaces.append(
            {
                "surface": row.surface,
                "computed_at": computed_at_value,
                "compute_seconds": float(row.compute_seconds)
                if row.compute_seconds is not None
                else None,
                "age_seconds": age_seconds,
                "payload": row.payload,
                "status": status,
            }
        )

    return {
        "generated_at": _isoformat(now),
        "threshold_seconds": max_age_seconds,
        "surfaces": surfaces,
        "counts": counts,
    }


# Compatibility aliases for callers using either convention.
get_freshness_sla_rollup = freshness_sla_rollup
get_sla_rollup = freshness_sla_rollup


@router.get("/freshness/sla-rollup")
def freshness_sla_rollup_endpoint(
    surface: Optional[str] = Query(default=None),
    max_age_seconds: int = Query(default=3600, ge=1, le=86400),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return freshness_sla_rollup(
        db=db,
        surface=surface,
        max_age_seconds=max_age_seconds,
    )


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
    FreshnessStat.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    test_db.add_all(
        [
            FreshnessStat(
                surface="fresh-surface",
                payload={"value": 1},
                computed_at=datetime.now(timezone.utc),
                compute_seconds=30.0,
            ),
            FreshnessStat(
                surface="stale-surface",
                payload={"value": 2},
                computed_at=datetime.now(timezone.utc).replace(year=2020),
                compute_seconds=120.0,
            ),
            FreshnessStat(
                surface="filtered-surface",
                payload={"value": 3},
                computed_at=datetime.now(timezone.utc),
                compute_seconds=45.0,
            ),
        ]
    )
    test_db.commit()

    def override_session():
        yield test_db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_session

    response = TestClient(test_app).get(
        "/api/freshness/sla-rollup", params={"max_age_seconds": 3600}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    statuses = {item["surface"]: item["status"] for item in body["surfaces"]}
    assert statuses["stale-surface"] == "stale"
    assert statuses["fresh-surface"] == "fresh"
    assert body["counts"]["stale"] >= 1

    filtered = TestClient(test_app).get(
        "/api/freshness/sla-rollup", params={"surface": "fresh-surface"}
    )
    assert filtered.status_code == 200
    assert len(filtered.json()["surfaces"]) == 1

    print("PASS")