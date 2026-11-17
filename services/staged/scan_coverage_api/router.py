"""HTTP surface for the scan coverage API.

Exposes `router` for spine mounting and delegates data access to the
service's relative logic module.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_coverage

router = APIRouter(prefix="/api", tags=["scan-coverage"])


class ScanCoverageBuckets(BaseModel):
    never_scanned: int
    scanned_24h: int
    scanned_7d: int
    scanned_30d: int
    stale_over_30d: int


class ScanCoverageResponse(BaseModel):
    total: int
    buckets: ScanCoverageBuckets
    tier_breakdown: dict[str, int]
    generated_at: datetime


@router.get(
    "/servers/scan-coverage",
    response_model=ScanCoverageResponse,
)
def scan_coverage(db: Session = Depends(get_session)) -> ScanCoverageResponse:
    result: dict[str, Any] = get_scan_coverage(db)
    return ScanCoverageResponse(**result)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    scan_count=None,
                    last_scanned=None,
                    last_seen=now - timedelta(days=45),
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="recent-zero",
                    scan_count=0,
                    last_scanned=None,
                    last_seen=now - timedelta(hours=1),
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="scanned-2h",
                    scan_count=1,
                    last_scanned=now - timedelta(hours=2),
                    last_seen=now - timedelta(hours=1),
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="scanned-5d",
                    scan_count=1,
                    last_scanned=now - timedelta(days=5),
                    last_seen=now - timedelta(days=1),
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="scanned-40d",
                    scan_count=1,
                    last_scanned=now - timedelta(days=40),
                    last_seen=now - timedelta(days=2),
                    risk_tier="low",
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session() -> Any:
            yield session

        test_app.dependency_overrides[get_session] = override_get_session

        from fastapi.testclient import TestClient

        response = TestClient(test_app).get("/api/servers/scan-coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 5, payload
        assert payload["buckets"]["never_scanned"] == 2, payload
        assert payload["buckets"]["stale_over_30d"] == 1, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()