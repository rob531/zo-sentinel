from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import scan_backlog_rows


router = APIRouter(prefix="/api/registry", tags=["registry"])


class ScanBacklogBucket(BaseModel):
    bucket: str
    count: int


class ScanBacklogResponse(BaseModel):
    total_servers: int
    never_scanned: int
    buckets: List[ScanBacklogBucket]
    oldest_last_scanned: Optional[str]
    avg_scan_count: float


@router.get("/scan-backlog", response_model=ScanBacklogResponse)
def get_scan_backlog(session: Session = Depends(get_session)) -> ScanBacklogResponse:
    return ScanBacklogResponse(**scan_backlog_rows(session))


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import insert
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    reference = datetime.now(timezone.utc).replace(microsecond=0)
    registry_rows = [
        {
            "server_id": "never-scanned",
            "last_scanned": None,
            "scan_count": 0,
            "first_seen": reference - timedelta(days=210),
        },
        {
            "server_id": "scanned-2-days",
            "last_scanned": reference - timedelta(days=2),
            "scan_count": 1,
            "first_seen": reference - timedelta(days=30),
        },
        {
            "server_id": "scanned-45-days",
            "last_scanned": reference - timedelta(days=45),
            "scan_count": 2,
            "first_seen": reference - timedelta(days=100),
        },
        {
            "server_id": "scanned-200-days",
            "last_scanned": reference - timedelta(days=200),
            "scan_count": 3,
            "first_seen": reference - timedelta(days=300),
        },
        {
            "server_id": "scanned-3-days",
            "last_scanned": reference - timedelta(days=3),
            "scan_count": 4,
            "first_seen": reference - timedelta(days=20),
        },
    ]

    with testing_session() as session:
        session.execute(insert(McpServerRegistry.__table__), registry_rows)
        session.commit()

    def override_get_session():
        with testing_session() as session:
            yield session

    self_test_app = FastAPI()
    self_test_app.include_router(router)
    self_test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(self_test_app) as client:
            response = client.get("/api/registry/scan-backlog")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total_servers"] == 5
            assert payload["never_scanned"] == 1
            bucket_counts = {
                bucket["bucket"]: bucket["count"] for bucket in payload["buckets"]
            }
            assert bucket_counts["0-7d"] == 2
            assert bucket_counts["31-90d"] == 1
            assert bucket_counts["90d+"] == 1
            assert isinstance(payload["avg_scan_count"], float)
    finally:
        engine.dispose()

    print("PASS")