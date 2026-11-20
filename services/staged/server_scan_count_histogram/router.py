"""HTTP surface for the server scan-count histogram service."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import scan_count_histogram

router = APIRouter(prefix="/api", tags=["registry"])


class ScanCountBucket(BaseModel):
    band: str
    count: int


class RiskTierCount(BaseModel):
    risk_tier: str
    count: int


class ScanCountHistogramResponse(BaseModel):
    total_servers: int
    buckets: list[ScanCountBucket]
    never_scanned: int
    scanned: int
    tier_mix: list[RiskTierCount]


@router.get(
    "/registry/scan-count-histogram",
    response_model=ScanCountHistogramResponse,
)
def get_scan_count_histogram(
    db: Session = Depends(get_session),
) -> ScanCountHistogramResponse:
    return ScanCountHistogramResponse.model_validate(scan_count_histogram(db))


def _self_test() -> None:
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    def _get_test_session():
        with session_factory() as session:
            yield session

    scan_counts = [0, 1, 2, 3, 5, 11]
    tiers = ["low", "medium", "high", "low", "medium", "high"]

    with session_factory() as session:
        for index, (scan_count, tier) in enumerate(zip(scan_counts, tiers)):
            session.add(
                McpServerRegistry(
                    server_id=f"selftest-server-{index}",
                    name=f"Selftest Server {index}",
                    registry_source="selftest",
                    scan_count=scan_count,
                    last_scanned=(
                        None
                        if index == 0
                        else datetime(2024, 1, 1, tzinfo=timezone.utc)
                    ),
                    last_seen=datetime(2024, 1, 2, tzinfo=timezone.utc),
                    risk_tier=tier,
                    verdict="selftest",
                )
            )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = _get_test_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-count-histogram")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 6
    assert {item["band"]: item["count"] for item in payload["buckets"]} == {
        "0": 1,
        "1": 1,
        "2-3": 2,
        "4-9": 1,
        "10+": 1,
    }
    assert payload["never_scanned"] == 1
    assert payload["scanned"] == 5
    assert sum(item["count"] for item in payload["tier_mix"]) == 6
    print("PASS")


if __name__ == "__main__":
    _self_test()