from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_coverage

router = APIRouter(prefix="/api", tags=["registry"])


class SourceScanCoverage(BaseModel):
    registry_source: str
    total_servers: int
    scanned_servers: int
    never_scanned: int
    stale_scanned: int
    avg_scan_count: float
    pct_covered: float


class ScanCoverageSummary(BaseModel):
    total_servers: int
    scanned_servers: int
    never_scanned: int
    stale_scanned: int
    pct_covered: float


class ScanCoverageResponse(BaseModel):
    days: int
    generated_at: datetime
    sources: List[SourceScanCoverage]
    summary: ScanCoverageSummary


@router.get("/registry/scan-coverage", response_model=ScanCoverageResponse)
async def scan_coverage(
    days: int = Query(30),
    session=Depends(get_session),
) -> ScanCoverageResponse:
    days = max(1, min(365, days))
    payload = get_scan_coverage(session=session, days=days)
    return ScanCoverageResponse.model_validate(payload)


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="recent-server",
                    registry_source="registry-a",
                    last_scanned=now - timedelta(days=1),
                    scan_count=4,
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    registry_source="registry-b",
                    last_scanned=now - timedelta(days=60),
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="unscanned-server",
                    registry_source="registry-a",
                    last_scanned=None,
                    scan_count=0,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/registry/scan-coverage",
        params={"days": 30},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["summary"]["total_servers"] == 3
    assert body["summary"]["never_scanned"] == 1
    assert 0 <= body["summary"]["pct_covered"] <= 100
    assert len(body["sources"]) == 2

    print("PASS")