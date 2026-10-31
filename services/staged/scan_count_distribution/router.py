from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import compute_scan_buckets

router = APIRouter(prefix="/api", tags=["scan-count-distribution"])


class ScanBucket(BaseModel):
    bucket: str
    count: int


class ScanCountDistribution(BaseModel):
    total: int
    buckets: list[ScanBucket]
    by_source: dict[str, dict[str, int]]
    never_scanned: int


@router.get(
    "/registry/scan-count-distribution",
    response_model=ScanCountDistribution,
)
def get_scan_count_distribution(
    session: Session = Depends(get_session),
) -> ScanCountDistribution:
    result = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.registry_source,
            McpServerRegistry.scan_count,
            McpServerRegistry.last_scanned,
        )
    ).mappings()

    rows = [dict(row) for row in result]
    return ScanCountDistribution(**compute_scan_buckets(rows))


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    seed_rows = [
        {
            "server_id": "server-null",
            "registry_source": "alpha",
            "scan_count": None,
            "last_scanned": None,
        },
        {
            "server_id": "server-zero",
            "registry_source": "alpha",
            "scan_count": 0,
            "last_scanned": datetime(2025, 1, 1),
        },
        {
            "server_id": "server-one",
            "registry_source": "beta",
            "scan_count": 1,
            "last_scanned": datetime(2025, 1, 2),
        },
        {
            "server_id": "server-three",
            "registry_source": "beta",
            "scan_count": 3,
            "last_scanned": datetime(2025, 1, 3),
        },
        {
            "server_id": "server-five",
            "registry_source": "alpha",
            "scan_count": 5,
            "last_scanned": datetime(2025, 1, 4),
        },
        {
            "server_id": "server-twelve",
            "registry_source": "beta",
            "scan_count": 12,
            "last_scanned": None,
        },
    ]

    with SessionLocal() as session:
        session.execute(insert(McpServerRegistry), seed_rows)
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    response = TestClient(test_app).get(
        "/api/registry/scan-count-distribution"
    )
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["total"] == 6
    assert sum(bucket["count"] for bucket in payload["buckets"]) == 6
    assert payload["never_scanned"] == sum(
        row["last_scanned"] is None for row in seed_rows
    )

    print("PASS")