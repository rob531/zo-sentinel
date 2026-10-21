from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api/scans")


class CadenceGapRow(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    last_scanned: datetime
    days_since_scan: int
    scan_count: int = 0


class CadenceGapResponse(BaseModel):
    days: int
    generated_at: datetime
    stale_count: int
    total_scanned: int
    rows: List[CadenceGapRow]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def get_cadence_gaps(
    session: Session,
    days: int = 30,
    limit: int = 100,
) -> CadenceGapResponse:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    servers = (
        session.execute(
            select(McpServerRegistry).where(
                McpServerRegistry.last_scanned.is_not(None)
            )
        )
        .scalars()
        .all()
    )

    total_scanned = len(servers)
    stale_rows: List[CadenceGapRow] = []

    for server in servers:
        scanned = _as_utc(server.last_scanned)
        elapsed = now - scanned
        days_since_scan = max(0, elapsed.days)

        if scanned <= cutoff or elapsed > timedelta(days=days):
            stale_rows.append(
                CadenceGapRow(
                    server_id=server.server_id,
                    name=server.name,
                    registry_source=server.registry_source,
                    risk_tier=server.risk_tier,
                    verdict=server.verdict,
                    last_scanned=server.last_scanned,
                    days_since_scan=days_since_scan,
                    scan_count=server.scan_count or 0,
                )
            )

    stale_rows.sort(key=lambda row: row.days_since_scan, reverse=True)

    return CadenceGapResponse(
        days=days,
        generated_at=now,
        stale_count=len(stale_rows),
        total_scanned=total_scanned,
        rows=stale_rows[:limit],
    )


@router.get("/cadence-gaps", response_model=CadenceGapResponse)
def cadence_gaps(
    days: int = Query(30, ge=1, le=3650),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> CadenceGapResponse:
    return get_cadence_gaps(session=session, days=days, limit=limit)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_db = TestSession()
    now = datetime.now(timezone.utc)
    test_db.add_all(
        [
            McpServerRegistry(
                server_id="fresh-server",
                name="Fresh Server",
                registry_source="seed",
                verdict="approved",
                risk_tier="low",
                last_scanned=now,
                scan_count=1,
            ),
            McpServerRegistry(
                server_id="stale-server",
                name="Stale Server",
                registry_source="seed",
                verdict="approved",
                risk_tier="medium",
                last_scanned=now - timedelta(days=90),
                scan_count=1,
            ),
        ]
    )
    test_db.commit()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/scans/cadence-gaps?days=30&limit=100")
        assert response.status_code == 200
        payload = response.json()
        assert payload["stale_count"] == 1
        assert payload["rows"][0]["server_id"] == "stale-server"
        assert payload["rows"][0]["days_since_scan"] >= 90
        assert all(row["server_id"] != "fresh-server" for row in payload["rows"])

    test_db.close()
    test_engine.dispose()
    print("PASS")