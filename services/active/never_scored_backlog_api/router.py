# deps: fastapi, pydantic, sqlalchemy
"""never_scored_backlog_api -- servers that have never been scored.

GET /api/scoring/backlog/never-scored
  Returns servers in mcp_server_registry that have NO entry in
  mcp_llm_axis_scores, broken down by registry_source and risk_tier.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select, exists
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["never_scored_backlog_api"])


# --------------------------------------------------------------------------- #
# Request / response shapes
# --------------------------------------------------------------------------- #

class ServerSample(BaseModel):
    server_id: str
    name: str | None
    registry_source: str | None
    url: str | None
    risk_tier: str | None


class SourceBreakdown(BaseModel):
    registry_source: str
    total: int
    by_risk_tier: List[dict]  # {risk_tier: str, count: int}


class NeverScoredBacklogResponse(BaseModel):
    as_of: str
    total_never_scored: int
    total_registry: int
    by_source: List[SourceBreakdown]
    sample_servers: List[ServerSample]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/scoring/backlog/never-scored", response_model=NeverScoredBacklogResponse)
def never_scored_backlog(
    sample_limit: int = Query(default=10, ge=1, le=100),
    db: Session = Depends(get_session),
) -> NeverScoredBacklogResponse:
    """
    Return servers in the registry that have never been scored (no row in
    mcp_llm_axis_scores), broken down by registry_source and risk_tier.
    """
    now = datetime.now(timezone.utc)

    # Total registry count
    total_registry: int = (
        db.execute(select(func.count(McpServerRegistry.server_id)))
        .scalar_one()
    ) or 0

    # Sub-query: server_ids that appear in mcp_llm_axis_scores
    scored_subq = (
        select(McpLlmAxisScore.server_id)
        .distinct()
        .subquery()
    )

    # Never-scored servers
    never_scored_q = (
        select(McpServerRegistry)
        .where(~McpServerRegistry.server_id.in_(scored_subq))
    )

    total_never_scored: int = (
        db.execute(
            select(func.count(McpServerRegistry.server_id))
            .where(~McpServerRegistry.server_id.in_(scored_subq))
        )
        .scalar_one()
    ) or 0

    never_scored_rows = db.execute(never_scored_q).scalars().all()

    # Group by registry_source -> risk_tier
    by_source_map: dict[str, dict[str, int]] = {}
    for srv in never_scored_rows:
        src = srv.registry_source or "UNKNOWN"
        tier = srv.risk_tier or "UNKNOWN"
        by_source_map.setdefault(src, {})
        by_source_map[src][tier] = by_source_map[src].get(tier, 0) + 1

    by_source: List[SourceBreakdown] = []
    for src, tiers in sorted(by_source_map.items()):
        total_for_src = sum(tiers.values())
        by_risk_tier = [
            {"risk_tier": t, "count": c}
            for t, c in sorted(tiers.items())
        ]
        by_source.append(SourceBreakdown(
            registry_source=src,
            total=total_for_src,
            by_risk_tier=by_risk_tier,
        ))

    # Sample servers (order by first_seen desc)
    sample_servers = [
        ServerSample(
            server_id=srv.server_id,
            name=srv.name,
            registry_source=srv.registry_source,
            url=srv.url,
            risk_tier=srv.risk_tier,
        )
        for srv in sorted(never_scored_rows, key=lambda x: x.first_seen or datetime.min, reverse=True)[
            :sample_limit
        ]
    ]

    return NeverScoredBacklogResponse(
        as_of=now.isoformat(),
        total_never_scored=total_never_scored,
        total_registry=total_registry,
        by_source=by_source,
        sample_servers=sample_servers,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(512),
                registry_source VARCHAR(64),
                url TEXT,
                description TEXT,
                trust_score FLOAT,
                verdict VARCHAR(64),
                verdict_reasoning TEXT,
                confidence FLOAT,
                last_assessed TIMESTAMP,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                scan_count INTEGER DEFAULT 0,
                risk_tier VARCHAR(32),
                metadata TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(128),
                axis_name VARCHAR(64),
                label VARCHAR(64),
                label_index INTEGER,
                probs TEXT,
                p_top FLOAT,
                p_critical FLOAT,
                p_danger FLOAT,
                escalated BOOLEAN,
                escalated_to VARCHAR(32),
                decision_rule_version VARCHAR(32),
                model_version VARCHAR(64),
                adapter_sha256 VARCHAR(80),
                scored_at TIMESTAMP
            )
        """))
        conn.commit()

    with engine.connect() as conn:
        for sid, name, src, tier in [
            ("srv1", "Alpha",   "npm",   "HIGH"),
            ("srv2", "Beta",    "npm",   "HIGH"),
            ("srv3", "Gamma",   "npm",   "MEDIUM"),
            ("srv4", "Delta",   "github","LOW"),
            ("srv5", "Epsilon", "github","LOW"),
        ]:
            conn.execute(text(
                "INSERT INTO mcp_server_registry VALUES (:sid, :name, :src, :url, NULL, NULL, NULL, NULL, NULL, NULL, :fs, NULL, NULL, 0, :tier, NULL)"
            ), {"sid": sid, "name": name, "src": src, "url": f"https://example.com/{sid}", "tier": tier, "fs": "2024-01-01 00:00:00"})
        for sid in ["srv1", "srv3"]:
            conn.execute(text(
                "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, model_version, label) VALUES (:sid, 'overall_risk', 'v1', 'MEDIUM')"
            ), {"sid": sid})
        conn.commit()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_session():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    resp = client.get("/api/scoring/backlog/never-scored?sample_limit=5")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["total_registry"] == 5, f"expected 5, got {data['total_registry']}"
    assert data["total_never_scored"] == 3, f"expected 3 never-scored, got {data['total_never_scored']}"

    by_src = {s["registry_source"]: s for s in data["by_source"]}
    assert "npm" in by_src, f"'npm' missing from by_source: {data['by_source']}"
    assert by_src["npm"]["total"] == 2, f"npm total expected 2, got {by_src['npm']['total']}"
    assert "github" in by_src, f"'github' missing from by_source: {data['by_source']}"
    assert by_src["github"]["total"] == 1, f"github total expected 1, got {by_src['github']['total']}"

    sample_ids = {s["server_id"] for s in data["sample_servers"]}
    assert sample_ids == {"srv2", "srv4", "srv5"}, f"sample mismatch: {sample_ids}"

    print("PASS")
