"""HTTP surface for registry scan coverage."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import compute_scan_coverage as _compute_scan_coverage
except ImportError:
    try:
        from .logic import get_scan_coverage as _compute_scan_coverage
    except ImportError:
        try:
            from .logic import registry_scan_coverage as _compute_scan_coverage
        except ImportError:
            from .logic import scan_coverage as _compute_scan_coverage

router = APIRouter(prefix="/api", tags=["registry"])


class RegistrySourceCoverage(BaseModel):
    registry_source: str | None
    total: int
    scanned_ever: int
    never_scanned: int
    scanned_last_24h: int
    stale: int
    median_scan_count: float


class NeverScannedServer(BaseModel):
    server_id: str
    name: str | None
    registry_source: str | None
    first_seen: datetime | None


class RegistryScanCoverageResponse(BaseModel):
    sources: list[RegistrySourceCoverage]
    never_scanned_sample: list[NeverScannedServer]
    generated_at: datetime


@router.get(
    "/registry/scan-coverage",
    response_model=RegistryScanCoverageResponse,
)
def get_registry_scan_coverage(
    db: Session = Depends(get_session),
) -> RegistryScanCoverageResponse:
    coverage: Any = _compute_scan_coverage(db)
    if isinstance(coverage, RegistryScanCoverageResponse):
        return coverage
    return RegistryScanCoverageResponse(**coverage)


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    with TestSession() as test_db:
        test_db.add_all(
            [
                McpServerRegistry(
                    server_id="never-scanned",
                    name="Never scanned server",
                    registry_source="source-a",
                    first_seen=now - timedelta(days=30),
                    last_seen=now,
                    last_scanned=None,
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="scanned-today",
                    name="Recently scanned server",
                    registry_source="source-a",
                    first_seen=now - timedelta(days=20),
                    last_seen=now,
                    last_scanned=now,
                    scan_count=3,
                ),
                McpServerRegistry(
                    server_id="scanned-old",
                    name="Stale scanned server",
                    registry_source="source-b",
                    first_seen=now - timedelta(days=40),
                    last_seen=now,
                    last_scanned=now - timedelta(days=10),
                    scan_count=5,
                ),
                McpServerRegistry(
                    server_id="scanned-recent",
                    name="Second recently scanned server",
                    registry_source="source-b",
                    first_seen=now - timedelta(days=15),
                    last_seen=now,
                    last_scanned=now,
                    scan_count=2,
                ),
            ]
        )
        test_db.commit()

        test_app = FastAPI()
        test_app.include_router(router)

        def override_get_session():
            yield test_db

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/registry/scan-coverage")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert len(payload["sources"]) == 2
            source_a = next(
                row
                for row in payload["sources"]
                if row["registry_source"] == "source-a"
            )
            source_b = next(
                row
                for row in payload["sources"]
                if row["registry_source"] == "source-b"
            )
            assert source_a["never_scanned"] >= 1
            assert source_b["stale"] >= 1
            assert any(
                server["server_id"] == "never-scanned"
                for server in payload["never_scanned_sample"]
            )

    print("PASS")