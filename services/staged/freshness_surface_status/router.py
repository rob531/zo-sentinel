from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import FreshnessStat
from .logic import get_freshness_surface_status


class FreshnessSurfaceEntry(BaseModel):
    surface: str
    computed_at: datetime
    compute_seconds: float
    payload_keys: int = Field(ge=0)
    age_seconds: float
    staleness: str


class FreshnessSurfaceCounts(BaseModel):
    fresh: int = Field(ge=0)
    aging: int = Field(ge=0)
    stale: int = Field(ge=0)


class FreshnessSurfaceStatusResponse(BaseModel):
    generated_at: datetime
    surfaces: List[FreshnessSurfaceEntry]
    counts: FreshnessSurfaceCounts
    worst_surface: Optional[str] = None


router = APIRouter(prefix="/api", tags=["freshness"])


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    raise TypeError("Freshness surface logic returned an unsupported value")


def _response_payload(value: Any) -> Dict[str, Any]:
    data = _as_dict(value)
    surfaces: List[Dict[str, Any]] = []

    for raw_surface in data.get("surfaces", []):
        surface = _as_dict(raw_surface)
        if "payload_keys" not in surface:
            surface["payload_keys"] = surface.get("payload_size", 0)
        surface.pop("payload_size", None)
        surfaces.append(surface)

    worst_surface = data.get("worst_surface")
    if isinstance(worst_surface, dict):
        worst_surface = worst_surface.get("surface")

    return {
        "generated_at": data.get("generated_at"),
        "surfaces": surfaces,
        "counts": data.get("counts", {}),
        "worst_surface": worst_surface,
    }


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfaceStatusResponse,
    status_code=200,
)
def get_freshness_surfaces(session=Depends(get_session)) -> FreshnessSurfaceStatusResponse:
    return FreshnessSurfaceStatusResponse(
        **_response_payload(get_freshness_surface_status(session))
    )


def _run_self_test() -> None:
    from datetime import timedelta, timezone

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
    testing_session = sessionmaker(bind=engine)

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="surface-fresh",
                    computed_at=now - timedelta(minutes=5),
                    compute_seconds=0.25,
                    payload={"value": 1},
                ),
                FreshnessStat(
                    surface="surface-aging",
                    computed_at=now - timedelta(hours=2),
                    compute_seconds=0.5,
                    payload={"value": 2, "detail": "ok"},
                ),
                FreshnessStat(
                    surface="surface-stale",
                    computed_at=now - timedelta(days=2),
                    compute_seconds=1.0,
                    payload={"value": 3},
                ),
            ]
        )
        session.commit()

    def override_get_session():
        session = testing_session()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["surfaces"]) == 3
        assert sum(body["counts"].values()) == 3
        assert body["counts"]["stale"] == 1
        assert all("payload_keys" in surface for surface in body["surfaces"])

    print("PASS")


if __name__ == "__main__":
    _run_self_test()