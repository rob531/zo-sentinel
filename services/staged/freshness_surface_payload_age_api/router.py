import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

from .logic import get_freshness_surface_payload_age

router = APIRouter(prefix="/api")


class FreshnessSurface(BaseModel):
    surface: str
    computed_at: Optional[str]
    age_hours: Optional[float]
    compute_seconds: Optional[float]
    payload_keys: List[str]
    payload_size_bytes: int
    is_stale: bool


class FreshnessSurfacesResponse(BaseModel):
    surfaces: List[FreshnessSurface]
    total_surfaces: int
    stale_count: int
    computed_at: str


@router.get(
    "/freshness/surfaces",
    response_model=FreshnessSurfacesResponse,
    summary="List freshness surface payload ages",
)
def list_freshness_surfaces(
    session: Session = Depends(get_session),
) -> FreshnessSurfacesResponse:
    result = get_freshness_surface_payload_age(session)

    if isinstance(result, list):
        surfaces = [dict(item) for item in result]
        payload: Dict[str, Any] = {
            "surfaces": surfaces,
            "total_surfaces": len(surfaces),
            "stale_count": sum(1 for item in surfaces if item.get("is_stale")),
            "computed_at": datetime.now(timezone.utc).isoformat(),
        }
    else:
        payload = dict(result)
        payload.setdefault("surfaces", [])
        payload.setdefault(
            "computed_at", datetime.now(timezone.utc).isoformat()
        )
        payload["total_surfaces"] = len(payload["surfaces"])
        payload["stale_count"] = sum(
            1 for item in payload["surfaces"] if item.get("is_stale")
        )

    return FreshnessSurfacesResponse(**payload)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE freshness_stats (
                    surface VARCHAR PRIMARY KEY,
                    payload TEXT,
                    computed_at DATETIME,
                    compute_seconds FLOAT
                )
                """
            )
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        connection.execute(
            text(
                """
                INSERT INTO freshness_stats
                    (surface, payload, computed_at, compute_seconds)
                VALUES
                    (:surface, :payload, :computed_at, :compute_seconds)
                """
            ),
            [
                {
                    "surface": "fresh_surface",
                    "payload": json.dumps({"alpha": 1, "beta": 2}),
                    "computed_at": now - timedelta(hours=1),
                    "compute_seconds": 0.25,
                },
                {
                    "surface": "stale_surface",
                    "payload": json.dumps({"gamma": 3}),
                    "computed_at": now - timedelta(hours=48),
                    "compute_seconds": 1.5,
                },
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/surfaces")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_surfaces"] == 2, body
        assert body["stale_count"] == 1, body
        fresh = next(
            surface
            for surface in body["surfaces"]
            if surface["surface"] == "fresh_surface"
        )
        assert len(fresh["payload_keys"]) == 2, body
        assert fresh["payload_size_bytes"] > 0, body

    test_engine.dispose()
    print("PASS")