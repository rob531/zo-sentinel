"""HTTP surface for freshness SLA monitoring."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_freshness_sla

router = APIRouter(prefix="/api", tags=["freshness"])


class SurfaceFreshness(BaseModel):
    surface: str
    computed_at: datetime
    age_seconds: float
    compute_seconds: float
    status: str


class FreshnessSLASummary(BaseModel):
    stale_surfaces: int
    missing_surfaces: int
    never_assessed_servers: int
    total_servers: int


class FreshnessSLAResponse(BaseModel):
    surfaces: list[SurfaceFreshness]
    summary: FreshnessSLASummary
    generated_at: datetime


@router.get("/freshness/sla", response_model=FreshnessSLAResponse)
def get_freshness_sla_endpoint(
    db: Session = Depends(get_session),
) -> FreshnessSLAResponse:
    payload = get_freshness_sla(db)
    return FreshnessSLAResponse(**payload)


if __name__ == "__main__":
    from datetime import timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import FreshnessStat, McpLlmAxisScore, McpServerRegistry

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)
    FreshnessStat.__table__.create(engine)

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)

    with testing_session() as db:
        db.add(
            McpServerRegistry(
                server_id="server-null",
                last_assessed=None,
                last_seen=now,
                last_scanned=now,
                scan_count=1,
                risk_tier="medium",
            )
        )
        db.add(
            McpServerRegistry(
                server_id="server-old",
                last_assessed=now - timedelta(days=8),
                last_seen=now,
                last_scanned=now,
                scan_count=1,
                risk_tier="medium",
            )
        )
        db.add(
            McpServerRegistry(
                server_id="server-fresh-1",
                last_assessed=now,
                last_seen=now,
                last_scanned=now,
                scan_count=1,
                risk_tier="low",
            )
        )
        db.add(
            McpServerRegistry(
                server_id="server-fresh-2",
                last_assessed=now,
                last_seen=now,
                last_scanned=now,
                scan_count=1,
                risk_tier="low",
            )
        )
        db.add(
            FreshnessStat(
                surface="stale-surface",
                payload={"max_age_seconds": 60},
                computed_at=now - timedelta(hours=1),
                compute_seconds=10,
            )
        )
        db.add(
            FreshnessStat(
                surface="fresh-surface",
                payload={"max_age_seconds": 3600},
                computed_at=now,
                compute_seconds=10,
            )
        )
        db.add(
            FreshnessStat(
                surface="empty-payload-surface",
                payload={},
                computed_at=now,
                compute_seconds=10,
            )
        )
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: testing_session()

    response = TestClient(test_app).get("/api/freshness/sla")
    response.raise_for_status()
    result = response.json()
    assert sum(s["status"] == "STALE" for s in result["surfaces"]) == 1
    assert result["summary"]["never_assessed_servers"] == 1
    assert result["summary"]["total_servers"] == 4
    print("PASS")