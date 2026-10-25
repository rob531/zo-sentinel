from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api", tags=["registry_scan_activity"])


class ScanActivityServer(BaseModel):
    server_id: str
    name: str
    registry_source: str
    scan_count: int
    last_scanned: Optional[str] = None
    risk_tier: Optional[str] = None


class ScanActivityResponse(BaseModel):
    days: int
    total_servers: int
    scanned_in_window: int
    never_scanned: int
    servers: List[ScanActivityServer]


def _to_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _iso(value):
    parsed = _to_datetime(value)
    if parsed is None:
        if value is None:
            return None
        return str(value)
    return parsed.astimezone(timezone.utc).isoformat()


@router.get("/registry/scan-activity", response_model=ScanActivityResponse)
def get_registry_scan_activity(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
):
    rows = db.query(McpServerRegistry).all()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total_servers = len(rows)
    scanned_in_window = 0
    never_scanned = 0
    for row in rows:
        if row.last_scanned is None:
            never_scanned += 1
            continue
        parsed = _to_datetime(row.last_scanned)
        if parsed is not None and parsed >= cutoff:
            scanned_in_window += 1

    sortable = [(row, _to_datetime(row.last_scanned)) for row in rows]
    non_null = [(row, parsed) for row, parsed in sortable if parsed is not None]
    null_rows = [row for row, parsed in sortable if parsed is None]
    non_null.sort(key=lambda item: item[1], reverse=True)
    ordered_rows = [row for row, _ in non_null] + null_rows
    ordered_rows = ordered_rows[:200]

    servers = []
    for row in ordered_rows:
        servers.append(
            ScanActivityServer(
                server_id=row.server_id,
                name=row.name,
                registry_source=row.registry_source,
                scan_count=0 if row.scan_count is None else row.scan_count,
                last_scanned=_iso(row.last_scanned),
                risk_tier=row.risk_tier,
            )
        )

    return {
        "days": days,
        "total_servers": total_servers,
        "scanned_in_window": scanned_in_window,
        "never_scanned": never_scanned,
        "servers": servers,
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with Session(engine) as seed_session:
        seed_session.add_all(
            [
                McpServerRegistry(
                    server_id="s1",
                    name="Server One",
                    registry_source="registry",
                    scan_count=2,
                    last_scanned=now - timedelta(days=1),
                    first_seen=now,
                    last_seen=now,
                    risk_tier="low",
                ),
                McpServerRegistry(
                    server_id="s2",
                    name="Server Two",
                    registry_source="registry",
                    scan_count=0,
                    last_scanned=None,
                    first_seen=now,
                    last_seen=now,
                    risk_tier=None,
                ),
                McpServerRegistry(
                    server_id="s3",
                    name="Server Three",
                    registry_source="registry",
                    scan_count=1,
                    last_scanned=now - timedelta(days=60),
                    first_seen=now,
                    last_seen=now,
                    risk_tier="high",
                ),
            ]
        )
        seed_session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)
    response = client.get("/api/registry/scan-activity")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["days"] == 30
    assert data["total_servers"] == 3
    assert data["never_scanned"] == 1
    assert data["scanned_in_window"] == 1
    assert len(data["servers"]) <= 200
    non_null = [item["last_scanned"] for item in data["servers"] if item["last_scanned"] is not None]
    assert non_null == sorted(non_null, reverse=True)
    assert data["servers"][-1]["last_scanned"] is None
    print("PASS")