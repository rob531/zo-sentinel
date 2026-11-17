from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_backlog_cohorts

router = APIRouter(prefix="/api", tags=["scan-backlog"])


class ScanBacklogCohort(BaseModel):
    cohort: str
    count: int
    by_source: Dict[str, int]


class ScanBacklogResponse(BaseModel):
    days: int
    cohorts: List[ScanBacklogCohort]
    total: int
    never_scanned: int


@router.get("/registry/scan-backlog", response_model=ScanBacklogResponse)
def get_scan_backlog(
    days: int = Query(default=30),
    db: Session = Depends(get_session),
) -> ScanBacklogResponse:
    clamped_days = max(1, min(365, days))
    result = get_scan_backlog_cohorts(db, clamped_days)
    return ScanBacklogResponse(**result)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    rows = [
        ("never-scanned", "Never scanned", None, 0, "source-a"),
        ("recent", "Recently scanned", now - timedelta(days=3), 1, "source-a"),
        ("overdue", "Overdue scan", now - timedelta(days=60), 1, "source-b"),
        ("stale", "Stale scan", now - timedelta(days=400), 1, "source-c"),
    ]
    with test_engine.begin() as connection:
        for server_id, name, last_scanned, scan_count, source in rows:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_server_registry
                        (server_id, name, scan_count, last_scanned, last_seen,
                         risk_tier, registry_source)
                    VALUES
                        (:server_id, :name, :scan_count, :last_scanned, :last_seen,
                         :risk_tier, :registry_source)
                    """
                ),
                {
                    "server_id": server_id,
                    "name": name,
                    "scan_count": scan_count,
                    "last_scanned": last_scanned,
                    "last_seen": now,
                    "risk_tier": "medium",
                    "registry_source": source,
                },
            )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/registry/scan-backlog")
    assert response.status_code == 200, response.text
    payload = response.json()
    cohorts = {item["cohort"]: item["count"] for item in payload["cohorts"]}
    assert payload["never_scanned"] == 1
    assert cohorts["scan_overdue"] == 1
    assert cohorts["scan_stale"] == 1
    assert payload["total"] == 4
    print("PASS")