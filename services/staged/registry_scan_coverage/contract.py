from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


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
    generated_at: str
    sources: List[SourceScanCoverage]
    summary: ScanCoverageSummary


router = APIRouter(prefix="/api")


def _coverage_rows(db: Session, cutoff: datetime):
    fresh = case(
        (McpServerRegistry.last_scanned.is_(None), 0),
        (McpServerRegistry.last_scanned >= cutoff, 1),
        else_=0,
    )
    stale = case(
        (McpServerRegistry.last_scanned.is_(None), 0),
        (McpServerRegistry.last_scanned < cutoff, 1),
        else_=0,
    )
    never = case((McpServerRegistry.last_scanned.is_(None), 1), else_=0)

    statement = (
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count().label("total_servers"),
            func.sum(fresh).label("scanned_servers"),
            func.sum(never).label("never_scanned"),
            func.sum(stale).label("stale_scanned"),
            func.avg(func.coalesce(McpServerRegistry.scan_count, 0)).label(
                "avg_scan_count"
            ),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    )
    return db.execute(statement).all()


def get_scan_coverage(
    days: int = Query(default=30),
    db: Session = Depends(get_session),
) -> ScanCoverageResponse:
    days = max(1, min(365, int(days)))
    generated_at = datetime.now(timezone.utc)
    cutoff = generated_at - timedelta(days=days)

    sources: List[SourceScanCoverage] = []
    total_servers = 0
    scanned_servers = 0
    never_scanned = 0
    stale_scanned = 0

    for row in _coverage_rows(db, cutoff):
        source_total = int(row.total_servers or 0)
        source_scanned = int(row.scanned_servers or 0)
        source_never = int(row.never_scanned or 0)
        source_stale = int(row.stale_scanned or 0)
        source_pct = (
            round((source_scanned / source_total) * 100.0, 2)
            if source_total
            else 0.0
        )

        sources.append(
            SourceScanCoverage(
                registry_source=str(row.registry_source),
                total_servers=source_total,
                scanned_servers=source_scanned,
                never_scanned=source_never,
                stale_scanned=source_stale,
                avg_scan_count=round(float(row.avg_scan_count or 0.0), 2),
                pct_covered=source_pct,
            )
        )
        total_servers += source_total
        scanned_servers += source_scanned
        never_scanned += source_never
        stale_scanned += source_stale

    pct_covered = (
        round((scanned_servers / total_servers) * 100.0, 2)
        if total_servers
        else 0.0
    )

    return ScanCoverageResponse(
        days=days,
        generated_at=generated_at.isoformat(),
        sources=sources,
        summary=ScanCoverageSummary(
            total_servers=total_servers,
            scanned_servers=scanned_servers,
            never_scanned=never_scanned,
            stale_scanned=stale_scanned,
            pct_covered=pct_covered,
        ),
    )


router.get("/registry/scan-coverage", response_model=ScanCoverageResponse)(
    get_scan_coverage
)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )

    McpServerRegistry.__table__.create(bind=engine)
    now = datetime.now(timezone.utc)

    with TestingSessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="recent-server",
                    last_scanned=now - timedelta(days=1),
                    scan_count=4,
                    last_seen=now,
                    risk_tier="low",
                    registry_source="alpha",
                    trust_score=0.9,
                ),
                McpServerRegistry(
                    server_id="stale-server",
                    last_scanned=now - timedelta(days=90),
                    scan_count=2,
                    last_seen=now,
                    risk_tier="medium",
                    registry_source="alpha",
                    trust_score=0.7,
                ),
                McpServerRegistry(
                    server_id="never-scanned-server",
                    last_scanned=None,
                    scan_count=0,
                    last_seen=now,
                    risk_tier="high",
                    registry_source="beta",
                    trust_score=0.4,
                ),
            ]
        )
        db.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-coverage", params={"days": 30})
        assert response.status_code == 200, response.text
        payload = response.json()

        assert payload["summary"]["total_servers"] == 3
        assert payload["summary"]["never_scanned"] == 1
        assert 0 <= payload["summary"]["pct_covered"] <= 100
        assert len(payload["sources"]) == len(
            {source["registry_source"] for source in payload["sources"]}
        )

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()