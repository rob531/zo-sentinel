# deps: fastapi, pydantic, sqlalchemy
"""Registry Growth Progress API.
Provides GET /registry/growth/progress returning registry growth metrics:
total servers, growth rate, scored vs unscored breakdown, and breakdown
by registry_source. Mirrors verdict_breakdown_api.py structure.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(tags=["registry_growth"])


class SourceBreakdown(BaseModel):
    source: str
    total: int
    scored: int
    unscored: int


class GrowthProgressResponse(BaseModel):
    total_servers: int
    scored_servers: int
    unscored_servers: int
    assessed_pct: float
    growth_rate_7d: float
    breakdown_by_source: List[SourceBreakdown]


def _latest_model_version(db: Session) -> str | None:
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row


@router.get("/registry/growth/progress", response_model=GrowthProgressResponse)
def get_registry_growth_progress(db: Session = Depends(get_session)) -> GrowthProgressResponse:
    """Return registry growth metrics and breakdown by source."""
    now = datetime.now(timezone.utc)

    # Total registry rows
    total_servers: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
    ).scalar_one() or 0

    # Scored servers for the latest model version
    mv = _latest_model_version(db)
    if mv and total_servers > 0:
        scored_servers: int = db.execute(
            select(func.count(func.distinct(McpLlmAxisScore.server_id)))
            .where(McpLlmAxisScore.model_version == mv)
        ).scalar_one() or 0
    else:
        scored_servers = 0

    unscored_servers = max(0, total_servers - scored_servers)
    assessed_pct = round((scored_servers / total_servers * 100), 2) if total_servers > 0 else 0.0

    # 7-day growth rate
    seven_days_ago = now - timedelta(days=7)
    servers_7d_ago: int = db.execute(
        select(func.count(McpServerRegistry.server_id))
        .where(McpServerRegistry.first_seen <= seven_days_ago)
    ).scalar_one() or 0
    delta_7d = total_servers - servers_7d_ago
    growth_rate_7d = round(delta_7d / 7, 2) if delta_7d >= 0 else 0.0

    # Breakdown by registry_source
    sources = db.execute(
        select(McpServerRegistry.registry_source).distinct()
    ).scalars().all()

    breakdown_by_source: List[SourceBreakdown] = []
    for src in sources:
        src_total: int = db.execute(
            select(func.count(McpServerRegistry.server_id))
            .where(McpServerRegistry.registry_source == src)
        ).scalar_one() or 0
        if mv:
            src_scored: int = db.execute(
                select(func.count(func.distinct(McpLlmAxisScore.server_id)))
                .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
                .where(
                    McpLlmAxisScore.model_version == mv,
                    McpServerRegistry.registry_source == src,
                )
            ).scalar_one() or 0
        else:
            src_scored = 0
        breakdown_by_source.append(SourceBreakdown(
            source=src or "(unknown)",
            total=src_total,
            scored=src_scored,
            unscored=max(0, src_total - src_scored),
        ))

    return GrowthProgressResponse(
        total_servers=total_servers,
        scored_servers=scored_servers,
        unscored_servers=unscored_servers,
        assessed_pct=assessed_pct,
        growth_rate_7d=growth_rate_7d,
        breakdown_by_source=breakdown_by_source,
    )


if __name__ == "__main__":  # CI-safe self-test
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    s = SessionLocal()
    # 5 servers across 2 sources
    s.add(McpServerRegistry(server_id="srv1", name="Srv 1", registry_source="npm"))
    s.add(McpServerRegistry(server_id="srv2", name="Srv 2", registry_source="npm"))
    s.add(McpServerRegistry(server_id="srv3", name="Srv 3", registry_source="github"))
    s.add(McpServerRegistry(server_id="srv4", name="Srv 4", registry_source="github"))
    s.add(McpServerRegistry(server_id="srv5", name="Srv 5", registry_source="github"))
    s.commit()

    # Score 3 of them with the same model version
    mv = "v3.0_40974559"
    for i, sid in enumerate(["srv1", "srv2", "srv3"], start=1):
        s.add(McpLlmAxisScore(
            id=i,
            server_id=sid,
            axis_name="overall_risk",
            label="HIGH",
            model_version=mv,
        ))
    s.commit()
    s.close()

    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    resp = client.get("/registry/growth/progress")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["total_servers"] == 5, data
    assert data["scored_servers"] == 3, data
    assert data["unscored_servers"] == 2, data
    assert data["assessed_pct"] == 60.0, data
    assert "growth_rate_7d" in data
    assert "breakdown_by_source" in data

    sources = {b["source"]: b for b in data["breakdown_by_source"]}
    assert sources["npm"]["total"] == 2, data
    assert sources["npm"]["scored"] == 2, data
    assert sources["github"]["total"] == 3, data
    assert sources["github"]["scored"] == 1, data

    # Edge case: empty registry
    engine2 = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine2)
    SessionLocal2 = sessionmaker(bind=engine2, autoflush=False, autocommit=False)

    def _override_empty():
        db = SessionLocal2()
        try:
            yield db
        finally:
            db.close()

    app2 = FastAPI()
    app2.include_router(router)
    app2.dependency_overrides[get_session] = _override_empty
    resp2 = TestClient(app2).get("/registry/growth/progress")
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    assert data2["total_servers"] == 0, data2
    assert data2["scored_servers"] == 0, data2
    assert data2["assessed_pct"] == 0.0, data2

    print("PASS")
