from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

from .logic import get_surface_timing as build_surface_timing_response

router = APIRouter(prefix="/api", tags=["freshness"])


class SurfaceTimingItem(BaseModel):
    surface: str
    compute_seconds: float | None = None
    computed_at: datetime | None = None
    payload_fields: dict[str, int | float] = {}


class SurfaceTimingResponse(BaseModel):
    surfaces: list[SurfaceTimingItem]
    total_surfaces: int


@router.get(
    "/freshness/surface-timing",
    response_model=SurfaceTimingResponse,
    operation_id="get_freshness_surface_timing",
)
def get_freshness_surface_timing(
    session: Session = Depends(get_session),
) -> SurfaceTimingResponse:
    return SurfaceTimingResponse.model_validate(
        build_surface_timing_response(session)
    )


def _run_self_test() -> None:
    from datetime import timezone

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
    test_session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    local_app = FastAPI()
    local_app.include_router(router)

    def override_get_session():
        with test_session_factory() as session:
            yield session

    local_app.dependency_overrides[get_session] = override_get_session

    with test_session_factory() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="threat_intel",
                    payload={"age_seconds": 12, "count": 3, "label": "ignored"},
                    computed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    compute_seconds=1.5,
                ),
                FreshnessStat(
                    surface="verdict_history",
                    payload={"age_seconds": 8, "count": 2},
                    computed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    compute_seconds=0.25,
                ),
            ]
        )
        session.commit()

    try:
        with TestClient(local_app) as client:
            response = client.get("/api/freshness/surface-timing")

        assert response.status_code == 200
        body = response.json()
        assert body["total_surfaces"] == 2
        assert len(body["surfaces"]) == 2
        assert any(
            surface["compute_seconds"] == 1.5
            for surface in body["surfaces"]
        )
        threat_surface = next(
            surface
            for surface in body["surfaces"]
            if surface["surface"] == "threat_intel"
        )
        assert threat_surface["payload_fields"] == {"age_seconds": 12, "count": 3}
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()