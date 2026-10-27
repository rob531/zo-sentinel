from datetime import date, datetime, time, timezone
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

try:
    from .logic import get_freshness_surface_payloads as _get_freshness_surface_payloads
except ImportError:
    try:
        from .logic import fetch_freshness_surface_payloads as _get_freshness_surface_payloads
    except ImportError:
        from .logic import get_freshness_surfaces as _get_freshness_surface_payloads


router = APIRouter()


def _timestamp(value: Any) -> float:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.min)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise TypeError(f"Unsupported computed_at value: {type(value).__name__}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _response_payload(result: Any) -> dict[str, Any]:
    if isinstance(result, Mapping) and "surfaces" in result:
        entries = result["surfaces"]
    else:
        entries = result

    surfaces: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise TypeError("Freshness surface entries must be mappings")
        surfaces.append(
            {
                "surface": entry["surface"],
                "computed_at": entry["computed_at"],
                "compute_seconds": entry["compute_seconds"],
                "payload": entry["payload"],
            }
        )

    stale_surfaces: list[str] = []
    if surfaces:
        newest = max(_timestamp(surface["computed_at"]) for surface in surfaces)
        stale_surfaces = [
            surface["surface"]
            for surface in surfaces
            if newest - _timestamp(surface["computed_at"]) > 3600
        ]

    return {"surfaces": surfaces, "stale_surfaces": stale_surfaces}


@router.get("/freshness/surfaces")
def get_freshness_surfaces(session: Session = Depends(get_session)) -> dict[str, Any]:
    return _response_payload(_get_freshness_surface_payloads(session))


if __name__ == "__main__":
    from datetime import timedelta

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
    FreshnessStat.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    newest = datetime(2025, 1, 1, 12, 0, 0)
    with TestingSession() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="alpha",
                    payload={"version": 1},
                    computed_at=newest - timedelta(hours=2),
                    compute_seconds=1.25,
                ),
                FreshnessStat(
                    surface="beta",
                    payload={"version": 2},
                    computed_at=newest - timedelta(hours=1, seconds=1),
                    compute_seconds=2.5,
                ),
                FreshnessStat(
                    surface="gamma",
                    payload={"version": 3},
                    computed_at=newest,
                    compute_seconds=3.75,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/freshness/surfaces")
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert len(body["stale_surfaces"]) >= 1
    print("PASS")