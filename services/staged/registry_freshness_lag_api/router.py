"""HTTP surface for registry freshness lag reporting."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_freshness_lag

router = APIRouter(prefix="/api", tags=["registry"])


@router.get("/registry/freshness/lag")
def registry_freshness_lag(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_freshness_lag(db, days)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def test_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = test_session

    now = datetime.utcnow()
    rows = [
        McpServerRegistry(
            server_id="fresh-1",
            name="Fresh One",
            registry_source="contract-source",
            first_seen=now - timedelta(days=1),
            last_seen=now - timedelta(minutes=5),
            last_scanned=now - timedelta(hours=1),
            scan_count=2,
            risk_tier="low",
        ),
        McpServerRegistry(
            server_id="fresh-2",
            name="Fresh Two",
            registry_source="contract-source",
            first_seen=now - timedelta(days=2),
            last_seen=now - timedelta(hours=2),
            last_scanned=now - timedelta(days=2),
            scan_count=1,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="fresh-3",
            name="Fresh Three",
            registry_source="contract-source",
            first_seen=now - timedelta(days=5),
            last_seen=now - timedelta(days=1),
            last_scanned=now - timedelta(days=10),
            scan_count=3,
            risk_tier="medium",
        ),
        McpServerRegistry(
            server_id="old-1",
            name="Very Old",
            registry_source="contract-source",
            first_seen=now - timedelta(days=400),
            last_seen=now - timedelta(days=200),
            last_scanned=now - timedelta(days=400),
            scan_count=7,
            risk_tier="high",
        ),
        McpServerRegistry(
            server_id="never-scanned",
            name="Never Scanned",
            registry_source="contract-source",
            first_seen=now - timedelta(days=3),
            last_seen=now - timedelta(hours=3),
            last_scanned=None,
            scan_count=0,
            risk_tier="low",
        ),
    ]

    with TestSession() as session:
        session.add_all(rows)
        session.commit()

    response = TestClient(test_app).get("/api/registry/freshness/lag", params={"days": 30})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["days"] == 30
    assert payload["sources"][0]["total"] == 5
    assert payload["sources"][0]["never_scanned"] == 1
    assert sum(payload["sources"][0]["buckets"].values()) == 5
    print("PASS")