"""Logic for the freshness SLO board over the real application database."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import CadenceJobRun, FreshnessStat

_FRESH_MAX_SECONDS = 3600
_STALE_MAX_SECONDS = 86400


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported datetime value: {type(value)!r}")

    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _isoformat(value: Any) -> Optional[str]:
    if value is None:
        return None
    return _as_utc(value).isoformat()


def _freshness_state(age_seconds: int) -> str:
    if age_seconds <= _FRESH_MAX_SECONDS:
        return "fresh"
    if age_seconds <= _STALE_MAX_SECONDS:
        return "stale"
    return "expired"


def get_freshness_slo_board(db: Session) -> Dict[str, Any]:
    """Return the latest freshness state per surface and latest run per job."""
    now = datetime.now(timezone.utc)

    freshness_rows = db.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
        )
    ).all()

    latest_by_surface: Dict[str, tuple[datetime, Any, Any]] = {}
    for surface, computed_at, compute_seconds in freshness_rows:
        computed = _as_utc(computed_at)
        current = latest_by_surface.get(surface)
        if current is None or computed >= current[0]:
            latest_by_surface[surface] = (computed, computed_at, compute_seconds)

    surfaces = []
    for surface in sorted(latest_by_surface):
        computed, original_computed_at, compute_seconds = latest_by_surface[surface]
        age_seconds = int((now - computed).total_seconds())
        surfaces.append(
            {
                "surface": surface,
                "computed_at": _isoformat(original_computed_at),
                "age_seconds": age_seconds,
                "compute_seconds": compute_seconds,
                "state": _freshness_state(age_seconds),
            }
        )

    cadence_rows = db.execute(
        select(
            CadenceJobRun.job,
            CadenceJobRun.status,
            CadenceJobRun.started_at,
            CadenceJobRun.finished_at,
            CadenceJobRun.rows_affected,
        )
    ).all()

    latest_by_job: Dict[str, tuple[datetime, Any, Any, Any, Any]] = {}
    for job, status, started_at, finished_at, rows_affected in cadence_rows:
        started = _as_utc(started_at)
        current = latest_by_job.get(job)
        if current is None or started >= current[0]:
            latest_by_job[job] = (started, status, started_at, finished_at, rows_affected)

    cadence = []
    for job in sorted(latest_by_job):
        _, status, started_at, finished_at, rows_affected = latest_by_job[job]
        cadence.append(
            {
                "job": job,
                "status": status,
                "started_at": _isoformat(started_at),
                "finished_at": _isoformat(finished_at),
                "rows_affected": rows_affected,
                "failed": status not in ("ok", "success"),
            }
        )

    return {
        "surfaces": surfaces,
        "cadence": cadence,
        "generated_at": now.isoformat(),
    }


def get_slo_board(db: Session) -> Dict[str, Any]:
    return get_freshness_slo_board(db)


def build_freshness_slo_board(db: Session) -> Dict[str, Any]:
    return get_freshness_slo_board(db)


def freshness_slo_board(db: Session) -> Dict[str, Any]:
    return get_freshness_slo_board(db)


if __name__ == "__main__":
    import asyncio
    from datetime import timedelta

    import httpx
    from fastapi import Depends, FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    CadenceJobRun.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="catalog",
                    payload={"source": "catalog"},
                    computed_at=now - timedelta(minutes=5),
                    compute_seconds=2,
                ),
                FreshnessStat(
                    surface="advisories",
                    payload={"source": "advisories"},
                    computed_at=now - timedelta(days=3),
                    compute_seconds=4,
                ),
                CadenceJobRun(
                    job="freshness_catalog",
                    status="ok",
                    started_at=now - timedelta(minutes=10),
                    finished_at=now - timedelta(minutes=9),
                    rows_affected=10,
                    detail="completed",
                ),
                CadenceJobRun(
                    job="freshness_advisories",
                    status="failed",
                    started_at=now - timedelta(hours=2),
                    finished_at=now - timedelta(hours=1, minutes=59),
                    rows_affected=0,
                    detail="failed",
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/freshness/slo-board")
    def freshness_slo_board_endpoint(db: Session = Depends(get_session)):
        return get_freshness_slo_board(db)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    async def request_board():
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get("/api/freshness/slo-board")

    response = asyncio.run(request_board())
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 2
    assert sum(item["state"] == "expired" for item in body["surfaces"]) == 1
    assert len(body["cadence"]) == 2
    assert sum(item["failed"] is True for item in body["cadence"]) == 1
    print("PASS")