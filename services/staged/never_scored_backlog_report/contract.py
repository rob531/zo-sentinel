# services/staged/never_scored_backlog_report/contract.py
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["reports"])


class NeverScoredSource(BaseModel):
    source: str
    count: int
    pct: float
    risk_tiers: dict[str, int]


class NeverScoredReport(BaseModel):
    total: int
    sources: list[NeverScoredSource]
    generated_at: datetime


def get_never_scored_report(db: Session) -> NeverScoredReport:
    total_query = text("""
        SELECT COUNT(*) as total
        FROM mcp_server_registry r
        LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
        WHERE s.id IS NULL
    """)
    total_result = db.execute(total_query).fetchone()
    total = total_result[0] if total_result else 0

    sources_query = text("""
        SELECT
            r.registry_source,
            r.risk_tier,
            COUNT(*) as count
        FROM mcp_server_registry r
        LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
        WHERE s.id IS NULL
        GROUP BY r.registry_source, r.risk_tier
        ORDER BY r.registry_source, r.risk_tier
    """)
    rows = db.execute(sources_query).fetchall()

    source_data: dict[str, dict[str, int]] = {}
    for source, tier, count in rows:
        if source not in source_data:
            source_data[source] = {}
        source_data[source][tier or "unknown"] = count

    sources = []
    for source in sorted(source_data.keys()):
        tier_counts = source_data[source]
        source_total = sum(tier_counts.values())
        pct = (source_total / total * 100) if total > 0 else 0.0
        sources.append(NeverScoredSource(
            source=source,
            count=source_total,
            pct=round(pct, 2),
            risk_tiers=tier_counts
        ))

    return NeverScoredReport(
        total=total,
        sources=sources,
        generated_at=datetime.now(timezone.utc)
    )


@router.get("/reports/never-scored-backlog", response_model=NeverScoredReport)
def never_scored_backlog_report(db: Session = Depends(get_session)):
    return get_never_scored_report(db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                confidence REAL,
                trust_score REAL,
                description TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TIMESTAMP,
                last_seen TIMESTAMP,
                last_scanned TIMESTAMP,
                last_assessed TIMESTAMP,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                probs TEXT,
                p_critical REAL,
                p_danger REAL,
                p_top REAL,
                model_version TEXT,
                decision_rule_version TEXT,
                scored_at TIMESTAMP,
                adapter_sha256 TEXT,
                escalated INTEGER,
                escalated_to TEXT
            )
        """))

    test_session_factory = sessionmaker(bind=engine)

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, registry_source, risk_tier)
            VALUES ('srv_scored_1', 'Scored Server One', 'npm', 'high')
        """))
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, registry_source, risk_tier)
            VALUES ('srv_scored_2', 'Scored Server Two', 'github', 'medium')
        """))
        conn.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, registry_source, risk_tier)
            VALUES ('srv_unscored', 'Never Scored Server', 'npm', 'low')
        """))
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, label_index, scored_at)
            VALUES ('srv_scored_1', 'safety', 'safe', 0, CURRENT_TIMESTAMP)
        """))
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, label_index, scored_at)
            VALUES ('srv_scored_2', 'safety', 'safe', 0, CURRENT_TIMESTAMP)
        """))

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/reports/never-scored-backlog")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["total"] == 1, f"Expected total=1, got {data['total']}"

    unscored_sources = [s for s in data["sources"] if s["source"] == "npm"]
    assert len(unscored_sources) == 1, "Expected unscored server in npm source"
    assert any(
        tier == "low" and count == 1
        for src in data["sources"]
        if src["source"] == "npm"
        for tier, count in src["risk_tiers"].items()
    ), "Expected unscored server with risk_tier=low"

    print("PASS")