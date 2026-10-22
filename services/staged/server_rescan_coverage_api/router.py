from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import rescan_coverage


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    total: int
    stale: int


class RescanCoverageResponse(BaseModel):
    stale_days: int
    total_servers: int
    never_scanned: int
    scanned_le_7d: int
    scanned_le_30d: int
    stale: int
    coverage_pct: float = Field(ge=0, le=100)
    by_registry_source: List[RegistrySourceCoverage]
    oldest_last_scanned: Optional[str] = None
    newest_last_scanned: Optional[str] = None


router = APIRouter(prefix="/api")


@router.get(
    "/registry/rescan-coverage",
    response_model=RescanCoverageResponse,
    summary="MCP server rescan coverage",
)
def get_rescan_coverage(
    db_session: Session = Depends(get_session),
    stale_days: int = Query(30, ge=0),
) -> RescanCoverageResponse:
    if db_session is None:
        raise HTTPException(status_code=400, detail="Database session is required")
    return rescan_coverage(db_session, stale_days=stale_days)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

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
    McpServerRegistry.__table__.create(engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = TestingSessionLocal()
    db.add_all(
        [
            McpServerRegistry(
                server_id="never-scanned",
                registry_source="alpha",
                last_scanned=None,
                scan_count=0,
            ),
            McpServerRegistry(
                server_id="scanned-recently",
                registry_source="alpha",
                last_scanned=now - timedelta(days=2),
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="scanned-stale",
                registry_source="beta",
                last_scanned=now - timedelta(days=40),
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="scanned-today",
                registry_source="beta",
                last_scanned=now,
                scan_count=1,
            ),
        ]
    )
    db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: db

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/rescan-coverage")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_servers"] == 4
            assert payload["never_scanned"] == 1
            assert payload["stale"] == 1
            assert 0 <= payload["coverage_pct"] <= 100
            assert sum(group["total"] for group in payload["by_registry_source"]) == 4
    finally:
        db.close()
        engine.dispose()

    print("PASS")