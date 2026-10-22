from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


DEFAULT_SLA_TARGET_SECONDS = 3600


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sla_target(payload: dict[str, Any]) -> int | float:
    value = payload.get("sla_target_seconds", DEFAULT_SLA_TARGET_SECONDS)
    if isinstance(value, bool):
        return DEFAULT_SLA_TARGET_SECONDS
    if isinstance(value, (int, float)):
        return value
    return DEFAULT_SLA_TARGET_SECONDS


def get_freshness_sla(
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    result = session.execute(
        select(FreshnessStat).order_by(
            FreshnessStat.surface,
            FreshnessStat.computed_at.desc(),
        )
    )
    rows = result.scalars().all()

    now = _utc_now()
    latest_by_surface: dict[str, FreshnessStat] = {}

    for row in rows:
        if row.surface not in latest_by_surface:
            latest_by_surface[row.surface] = row

    surfaces: list[dict[str, Any]] = []

    for surface, row in latest_by_surface.items():
        payload = _payload_dict(row.payload)
        target = _sla_target(payload)

        if row.computed_at is None:
            age_seconds = 0.0
            computed_at = None
        else:
            computed_at = row.computed_at
            age_seconds = max(
                0.0,
                (now - _as_utc(computed_at)).total_seconds(),
            )

        surfaces.append(
            {
                "surface": surface,
                "computed_at": computed_at.isoformat() if computed_at else None,
                "age_seconds": age_seconds,
                "within_sla": bool(age_seconds <= target),
                "sla_target_seconds": target,
                "payload": payload,
            }
        )

    surfaces.sort(key=lambda item: item["age_seconds"], reverse=True)
    return surfaces


def get_freshness_sla_compliance(
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return get_freshness_sla(session)


def get_freshness_summary(
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    return get_freshness_sla(session)


def build_freshness_sla_response(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    surfaces = get_freshness_sla(session)
    return {
        "surfaces": surfaces,
        "stale_count": sum(
            1 for surface in surfaces if not surface["within_sla"]
        ),
        "checked_at": _utc_now().isoformat(),
    }


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine)
    FreshnessStat.__table__.create(bind=engine)

    def override_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/freshness/sla")
    def freshness_sla(session: Session = Depends(get_session)):
        return build_freshness_sla_response(session)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = TestingSession()
    db.add_all(
        [
            FreshnessStat(
                surface="fresh",
                payload={},
                computed_at=now,
                compute_seconds=0.1,
            ),
            FreshnessStat(
                surface="stale",
                payload={},
                computed_at=now.replace(year=now.year - 2),
                compute_seconds=0.1,
            ),
            FreshnessStat(
                surface="custom",
                payload={"sla_target_seconds": 7200},
                computed_at=now,
                compute_seconds=0.1,
            ),
        ]
    )
    db.commit()
    db.close()

    response = TestClient(test_app).get("/api/freshness/sla")
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert sum(not item["within_sla"] for item in body["surfaces"]) == 1
    assert body["stale_count"] == 1
    print("PASS")


if __name__ == "__main__":
    _self_test()