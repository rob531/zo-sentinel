# deps: fastapi, sqlalchemy, pydantic
"""Registry Source Freshness Reporting Service.

GET /api/registry_source_freshness_reporting
    Groups mcp_server_registry by registry_source, returns the most-recent
    last_scanned timestamp and a "fresh" flag (scanned within 7 days) per source.

Auth : public.
Data  : app-db via get_session + SQLAlchemy ORM on McpServerRegistry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

# Ensure project root so `app` resolves when run as a script.
_root = Path(__file__).resolve().parents[2]
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

# Lazy imports so the path fix above takes effect before resolution.
from app.db import get_session  # noqa: E402
from app.models import McpServerRegistry  # noqa: E402
from sqlalchemy import func  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

router = APIRouter(prefix="/api", tags=["registry_source_freshness_reporting"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #

class RegistrySourceFreshness(BaseModel):
    registry_source: str = Field(..., description="Name of the registry source")
    last_scanned: datetime = Field(..., description="Most recent scan timestamp for this source")
    fresh: bool = Field(..., description="True when last_scanned is within 7 days")

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _is_fresh(ts: datetime) -> bool:
    now = datetime.now(timezone.utc)
    return (now - ts) <= timedelta(days=7)


# --------------------------------------------------------------------------- #
# Endpoint
# --------------------------------------------------------------------------- #

@router.get(
    "/registry_source_freshness_reporting",
    response_model=List[RegistrySourceFreshness],
    summary="Per-source scan freshness",
)
def get_registry_source_freshness(db: Session = Depends(get_session)):
    """
    Groups servers by ``registry_source`` and returns, for each distinct source:
      - the most-recent ``last_scanned`` timestamp
      - a boolean ``fresh`` flag (True when that timestamp is within 7 days)
    """
    rows = (
        db.query(
            McpServerRegistry.registry_source,
            func.max(McpServerRegistry.last_scanned).label("last_scanned"),
        )
        .group_by(McpServerRegistry.registry_source)
        .all()
    )

    result: List[RegistrySourceFreshness] = []
    for registry_source, last_scanned in rows:
        if last_scanned is None:
            fresh = False
            last_scanned = datetime.fromtimestamp(0, tz=timezone.utc)
        else:
            fresh = _is_fresh(last_scanned)
        result.append(
            RegistrySourceFreshness(
                registry_source=registry_source,
                last_scanned=last_scanned,
                fresh=fresh,
            )
        )
    return result


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys as _sys

    _project_root = Path(__file__).resolve().parents[2]
    if str(_project_root) not in _sys.path:
        _sys.path.insert(0, str(_project_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base  # type: ignore[attr-defined]

    # In-memory SQLite for testing.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def _seed(db: Session) -> None:
        now = datetime.now(timezone.utc)
        recent = now - timedelta(days=2)
        old = now - timedelta(days=30)
        servers = [
            McpServerRegistry(
                server_id="srv1",
                name="Server One",
                registry_source="source_a",
                url="https://example.com/a",
                description="",
                trust_score=0.9,
                verdict="",
                verdict_reasoning="",
                confidence=1.0,
                risk_tier="low",
                scan_count=1,
                first_seen=now,
                last_seen=now,
                last_scanned=recent,
                last_assessed=now,
                meta={},
            ),
            McpServerRegistry(
                server_id="srv2",
                name="Server Two",
                registry_source="source_a",
                url="https://example.com/a2",
                description="",
                trust_score=0.8,
                verdict="",
                verdict_reasoning="",
                confidence=1.0,
                risk_tier="medium",
                scan_count=1,
                first_seen=now,
                last_seen=now,
                last_scanned=old,  # older than 7 days -- source_a still "fresh" via srv1
                last_assessed=now,
                meta={},
            ),
            McpServerRegistry(
                server_id="srv3",
                name="Server Three",
                registry_source="source_b",
                url="https://example.com/b",
                description="",
                trust_score=0.7,
                verdict="",
                verdict_reasoning="",
                confidence=1.0,
                risk_tier="high",
                scan_count=1,
                first_seen=now,
                last_seen=now,
                last_scanned=recent,
                last_assessed=now,
                meta={},
            ),
        ]
        db.add_all(servers)
        db.commit()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as s:
        _seed(s)

    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/registry_source_freshness_reporting")
    if resp.status_code != status.HTTP_200_OK:
        print(f"FAIL: status {resp.status_code} -- {resp.text}")
        _sys.exit(1)

    data = resp.json()
    if len(data) != 2:
        print(f"FAIL: expected 2 sources, got {len(data)}")
        _sys.exit(1)

    fresh_map = {d["registry_source"]: d["fresh"] for d in data}
    if fresh_map.get("source_a") is not True:
        print("FAIL: source_a should be fresh (max scan is 2 days old)")
        _sys.exit(1)
    if fresh_map.get("source_b") is not True:
        print("FAIL: source_b should be fresh")
        _sys.exit(1)

    print("PASS")
