from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_cohorts


router = APIRouter(prefix="/api")


class ScanCohort(BaseModel):
    cohort: str
    servers: int
    distinct_risk_tiers: int
    median_scan_count: Optional[float]


class RegistrySource(BaseModel):
    registry_source: str
    servers: int


class ScanCohortResponse(BaseModel):
    total_servers: int
    cohorts: List[ScanCohort]
    sources: List[RegistrySource]


@router.get(
    "/registry/scan-cohorts",
    response_model=ScanCohortResponse,
    summary="Summarise MCP registry scan coverage cohorts",
)
def get_registry_scan_cohorts(
    registry_source: Optional[str] = Query(
        default=None,
        description="Optional registry source to filter by",
    ),
    limit: int = Query(default=50, ge=1),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    result = get_scan_cohorts(
        session,
        registry_source=registry_source,
        limit=limit,
    )
    return result


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT,
                    scan_count INTEGER,
                    last_scanned TEXT,
                    last_seen TEXT,
                    first_seen TEXT,
                    risk_tier TEXT,
                    verdict TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id,
                    name,
                    registry_source,
                    scan_count,
                    last_scanned,
                    last_seen,
                    first_seen,
                    risk_tier,
                    verdict
                ) VALUES (
                    :server_id,
                    :name,
                    :registry_source,
                    :scan_count,
                    :last_scanned,
                    :last_seen,
                    :first_seen,
                    :risk_tier,
                    :verdict
                )
                """
            ),
            [
                {
                    "server_id": "s1",
                    "name": "server-1",
                    "registry_source": "source-a",
                    "scan_count": None,
                    "last_scanned": None,
                    "last_seen": "2024-01-01",
                    "first_seen": "2023-01-01",
                    "risk_tier": "low",
                    "verdict": "approved",
                },
                {
                    "server_id": "s2",
                    "name": "server-2",
                    "registry_source": "source-a",
                    "scan_count": 0,
                    "last_scanned": None,
                    "last_seen": "2024-01-02",
                    "first_seen": "2023-01-02",
                    "risk_tier": "high",
                    "verdict": "review",
                },
                {
                    "server_id": "s3",
                    "name": "server-3",
                    "registry_source": "source-a",
                    "scan_count": 1,
                    "last_scanned": "2024-01-03",
                    "last_seen": "2024-01-03",
                    "first_seen": "2023-01-03",
                    "risk_tier": "low",
                    "verdict": "approved",
                },
                {
                    "server_id": "s4",
                    "name": "server-4",
                    "registry_source": "source-b",
                    "scan_count": 3,
                    "last_scanned": "2024-01-04",
                    "last_seen": "2024-01-04",
                    "first_seen": "2023-01-04",
                    "risk_tier": "medium",
                    "verdict": "review",
                },
                {
                    "server_id": "s5",
                    "name": "server-5",
                    "registry_source": "source-b",
                    "scan_count": 4,
                    "last_scanned": "2024-01-05",
                    "last_seen": "2024-01-05",
                    "first_seen": "2023-01-05",
                    "risk_tier": "medium",
                    "verdict": "review",
                },
                {
                    "server_id": "s6",
                    "name": "server-6",
                    "registry_source": "source-b",
                    "scan_count": 9,
                    "last_scanned": "2024-01-06",
                    "last_seen": "2024-01-06",
                    "first_seen": "2023-01-06",
                    "risk_tier": "high",
                    "verdict": "blocked",
                },
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-cohorts")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["cohorts"]) == 4
    assert payload["total_servers"] == 6
    never_scanned = next(
        cohort for cohort in payload["cohorts"] if cohort["cohort"] == "never_scanned"
    )
    assert never_scanned["servers"] == 2
    print("PASS")