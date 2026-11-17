from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_count_distribution


router = APIRouter(
    prefix="/api",
    tags=["registry-scan-count-distribution"],
)


class ScanBucket(BaseModel):
    bucket: str
    count: int


class SourceCount(BaseModel):
    registry_source: str
    count: int


class ScanCountDistributionResponse(BaseModel):
    scan_buckets: list[ScanBucket]
    by_source: list[SourceCount]
    never_scanned: int
    total: int


@router.get(
    "/registry/scan-count-distribution",
    response_model=ScanCountDistributionResponse,
)
def registry_scan_count_distribution(
    session: Session = Depends(get_session),
) -> ScanCountDistributionResponse:
    return get_scan_count_distribution(session)


if __name__ == "__main__":
    from datetime import datetime, timezone

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
    TestingSessionLocal = sessionmaker(
        bind=test_engine,
        autoflush=False,
        autocommit=False,
    )

    McpServerRegistry.__table__.create(bind=test_engine)

    with TestingSessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="scan-distribution-test-1",
                    scan_count=0,
                    registry_source="seed-a",
                    last_scanned=None,
                ),
                McpServerRegistry(
                    server_id="scan-distribution-test-2",
                    scan_count=1,
                    registry_source="seed-a",
                    last_scanned=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="scan-distribution-test-3",
                    scan_count=5,
                    registry_source="seed-b",
                    last_scanned=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="scan-distribution-test-4",
                    scan_count=12,
                    registry_source="seed-b",
                    last_scanned=datetime.now(timezone.utc),
                ),
            ]
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/registry/scan-count-distribution"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["never_scanned"] == 1
    print("PASS")