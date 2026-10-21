from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_cadence_gaps as fetch_cadence_gaps

router = APIRouter()


class CadenceGapRow(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    last_scanned: datetime
    days_since_scan: int
    scan_count: int


class CadenceGapResponse(BaseModel):
    days: int
    generated_at: datetime
    stale_count: int
    total_scanned: int
    rows: list[CadenceGapRow]


@router.get(
    "/api/scans/cadence-gaps",
    response_model=CadenceGapResponse,
)
def cadence_gaps(
    days: int = Query(default=30, ge=1, le=3650),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict:
    return fetch_cadence_gaps(session, days=days, limit=limit)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    now = datetime.utcnow()
    with testing_session() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="fresh-server",
                    name="Fresh Server",
                    registry_source="test",
                    risk_tier="low",
                    verdict="trusted",
                    last_scanned=now,
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    name="Stale Server",
                    registry_source="test",
                    risk_tier="high",
                    verdict="review",
                    last_scanned=now - timedelta(days=90),
                    scan_count=1,
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/scans/cadence-gaps",
            params={"days": 30, "limit": 100},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["stale_count"] == 1, payload
        assert payload["rows"], payload
        stale_row = payload["rows"][0]
        assert stale_row["server_id"] == "stale-server", payload
        assert stale_row["days_since_scan"] >= 90, payload
        assert all(
            row["server_id"] != "fresh-server" for row in payload["rows"]
        ), payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()