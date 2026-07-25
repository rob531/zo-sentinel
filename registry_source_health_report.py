# deps: fastapi, pydantic, sqlalchemy, requests
"""Registry Source Health Report API

Provides a FastAPI router exposing GET /registry-source-health.
Returns per-registry-source metrics (server count, scan stats, freshness)
from mcp_server_registry.

Mirrors the structure of ``verdict_breakdown_api.py`` but without
authentication or write side‑effects.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry

WRITE_SERVICE = "http://127.0.0.1:8772"
router = APIRouter(prefix="/api", tags=["registry_source_health"])


class SourceHealthItem(BaseModel):
    registry_source: str
    server_count: int
    avg_scan_count: float
    max_last_scanned: Optional[str] = None
    min_last_scanned: Optional[str] = None
    oldest_scan_hours_ago: Optional[float] = None


def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


@router.get("/registry-source-health", response_model=List[SourceHealthItem])
def get_registry_source_health(
    db: Session = Depends(get_session),
) -> List[SourceHealthItem]:
    """Per‑registry‑source health and freshness report.

    Aggregates ``mcp_server_registry`` by ``registry_source`` and returns:
    - ``server_count``       – number of servers in that source
    - ``avg_scan_count``     – mean scan_count across those servers
    - ``max_last_scanned``   – most‑recent scan timestamp (ISO‑8601)
    - ``min_last_scanned``   – oldest scan timestamp (ISO‑8601)
    - ``oldest_scan_hours_ago`` – how many hours since the oldest scan
    """
    rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            func.count().label("server_count"),
            func.avg(McpServerRegistry.scan_count).label("avg_scan_count"),
            func.max(McpServerRegistry.last_scanned).label("max_last_scanned"),
            func.min(McpServerRegistry.last_scanned).label("min_last_scanned"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(func.count().desc())
    ).all()

    now = datetime.now(timezone.utc)
    results: List[SourceHealthItem] = []
    for row in rows:
        source = row[0] or "unknown"
        max_scanned = row[3]
        min_scanned = row[4]
        oldest_hours: Optional[float] = None
        if min_scanned is not None:
            if min_scanned.tzinfo is None:
                min_scanned = min_scanned.replace(tzinfo=timezone.utc)
            delta = now - min_scanned
            oldest_hours = delta.total_seconds() / 3600.0

        results.append(
            SourceHealthItem(
                registry_source=source,
                server_count=row[1],
                avg_scan_count=round(row[2], 2) if row[2] is not None else 0.0,
                max_last_scanned=_to_iso(max_scanned),
                min_last_scanned=_to_iso(min_scanned),
                oldest_scan_hours_ago=round(oldest_hours, 2) if oldest_hours is not None else None,
            )
        )
    return results


if __name__ == "__main__":  # CI‑safe self‑test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite for testing
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed two registry sources with realistic timestamps
    now_ts = datetime.now(timezone.utc)
    db = SessionLocal()
    db.add(McpServerRegistry(
        server_id="s1",
        name="Server One",
        registry_source="github",
        url="https://example.com/one",
        scan_count=5,
        last_scanned=now_ts,
    ))
    db.add(McpServerRegistry(
        server_id="s2",
        name="Server Two",
        registry_source="github",
        url="https://example.com/two",
        scan_count=10,
        last_scanned=now_ts,
    ))
    db.add(McpServerRegistry(
        server_id="s3",
        name="Server Three",
        registry_source="npm",
        url="https://example.com/three",
        scan_count=3,
        last_scanned=now_ts,
    ))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    resp = client.get("/api/registry-source-health")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert isinstance(data, list), "Response is not a list"

    # Build a lookup by source name
    by_source = {item["registry_source"]: item for item in data}

    # Verify github source
    assert "github" in by_source, f"github not in sources: {list(by_source.keys())}"
    gh = by_source["github"]
    assert gh["server_count"] == 2, f"expected 2, got {gh['server_count']}"
    assert gh["avg_scan_count"] == 7.5, f"expected 7.5, got {gh['avg_scan_count']}"
    assert gh["max_last_scanned"] is not None
    assert gh["min_last_scanned"] is not None
    assert gh["oldest_scan_hours_ago"] is not None
    assert gh["oldest_scan_hours_ago"] >= 0, "oldest_scan_hours_ago must be >= 0"

    # Verify npm source
    assert "npm" in by_source, f"npm not in sources: {list(by_source.keys())}"
    np = by_source["npm"]
    assert np["server_count"] == 1, f"expected 1, got {np['server_count']}"
    assert np["oldest_scan_hours_ago"] is not None
    assert np["oldest_scan_hours_ago"] >= 0

    print("PASS")
