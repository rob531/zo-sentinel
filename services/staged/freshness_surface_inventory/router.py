"""HTTP surface for live Sentinel data-surface freshness inventory."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat

from .logic import get_freshness_surfaces

router = APIRouter(prefix="/api", tags=["freshness_surface_inventory"])


class SurfaceFreshness(BaseModel):
    surface: str
    computed_at: datetime
    compute_seconds: float
    age_seconds: float
    status: Literal["fresh", "stale", "expired"]


class FreshnessSummary(BaseModel):
    total: int
    fresh: int
    stale: int
    expired: int
    oldest_age_seconds: float


class FreshnessSurfacesResponse(BaseModel):
    surfaces: list[SurfaceFreshness]
    summary: FreshnessSummary


@router.get("/freshness/surfaces", response_model=FreshnessSurfacesResponse)
def freshness_surfaces(
    db: Session = Depends(get_session),
) -> FreshnessSurfacesResponse:
    inventory = get_freshness_surfaces(db)
    if isinstance(inventory, tuple):
        surfaces, summary = inventory
    else:
        surfaces = inventory["surfaces"]
        summary = inventory["summary"]
    return FreshnessSurfacesResponse(surfaces=surfaces, summary=summary)


def _self_test() -> None:
    from fastapi import FastAPI
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

    db = TestingSession()
    now = datetime.utcnow()
    db.add_all(
        [
            FreshnessStat(
                surface="fresh_surface",
                payload=json.dumps({"source": "test"}),
                computed_at=now - timedelta(seconds=60),
                compute_seconds=0.25,
            ),
            FreshnessStat(
                surface="stale_surface",
                payload=json.dumps({"source": "test"}),
                computed_at=now - timedelta(seconds=7200),
                compute_seconds=0.5,
            ),
            FreshnessStat(
                surface="expired_surface",
                payload=json.dumps({"source": "test"}),
                computed_at=now - timedelta(seconds=172800),
                compute_seconds=1.0,
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield db

    test_app.dependency_overrides[get_session] = override_get_session

    async def request() -> tuple[int, dict]:
        status_code = 0
        body = b""
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/freshness/surfaces",
            "raw_path": b"/api/freshness/surfaces",
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"testserver")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            nonlocal status_code, body
            if message["type"] == "http.response.start":
                status_code = message["status"]
            elif message["type"] == "http.response.body":
                body += message.get("body", b"")

        await test_app(scope, receive, send)
        return status_code, json.loads(body)

    status_code, response = asyncio.run(request())
    assert status_code == 200
    assert len(response["surfaces"]) == 3
    assert response["summary"]["total"] == 3
    assert response["summary"]["fresh"] == 1
    assert response["summary"]["stale"] == 1
    assert response["summary"]["expired"] == 1
    expired = next(item for item in response["surfaces"] if item["surface"] == "expired_surface")
    assert expired["status"] == "expired"
    db.close()


if __name__ == "__main__":
    _self_test()
    print("PASS")