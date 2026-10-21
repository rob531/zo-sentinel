from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_coverage as compute_scan_coverage

router = APIRouter(prefix="/api")


class SourceScanCoverage(BaseModel):
    registry_source: str
    total: int
    never_scanned: int
    stale: int
    avg_scan_count: float


class ScanCoverageResponse(BaseModel):
    total: int
    never_scanned: int
    stale: int
    fresh: int
    coverage_pct: float
    by_source: list[SourceScanCoverage]
    generated_at: datetime


@router.get(
    "/registry/scan-coverage",
    response_model=ScanCoverageResponse,
    summary="Calculate registry scan coverage and staleness",
)
def scan_coverage(
    max_age_days: Annotated[int, Query(ge=1, le=365)] = 30,
    session: Session = Depends(get_session),
) -> ScanCoverageResponse:
    return compute_scan_coverage(session, max_age_days)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    rows = [
        McpServerRegistry(
            server_id="server-never-scanned",
            name="Never scanned server",
            registry_source="source-a",
            risk_tier="medium",
            first_seen=now - timedelta(days=90),
            last_seen=now,
            last_scanned=None,
            scan_count=0,
        ),
        McpServerRegistry(
            server_id="server-stale",
            name="Stale server",
            registry_source="source-a",
            risk_tier="high",
            first_seen=now - timedelta(days=120),
            last_seen=now,
            last_scanned=now - timedelta(days=40),
            scan_count=2,
        ),
        McpServerRegistry(
            server_id="server-recent-a",
            name="Recent server A",
            registry_source="source-a",
            risk_tier="low",
            first_seen=now - timedelta(days=20),
            last_seen=now,
            last_scanned=now - timedelta(days=2),
            scan_count=4,
        ),
        McpServerRegistry(
            server_id="server-recent-b",
            name="Recent server B",
            registry_source="source-b",
            risk_tier="medium",
            first_seen=now - timedelta(days=30),
            last_seen=now,
            last_scanned=now - timedelta(days=5),
            scan_count=3,
        ),
        McpServerRegistry(
            server_id="server-recent-c",
            name="Recent server C",
            registry_source="source-b",
            risk_tier="low",
            first_seen=now - timedelta(days=10),
            last_seen=now,
            last_scanned=now - timedelta(days=1),
            scan_count=5,
        ),
    ]

    with session_factory() as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/registry/scan-coverage",
        params={"max_age_days": 30},
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["total"] == 5
    assert payload["never_scanned"] == 1
    assert payload["stale"] == 1
    assert payload["fresh"] == 3
    assert payload["coverage_pct"] == 80.0
    assert sum(source["total"] for source in payload["by_source"]) == 5
    assert datetime.fromisoformat(payload["generated_at"].replace("Z", "+00:00"))

    print("PASS")


if __name__ == "__main__":
    _self_test()