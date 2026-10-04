"""Trust summary service contract."""
import sys
from datetime import datetime, timezone
from typing import List, Optional
from decimal import Decimal

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore


class TierStats(BaseModel):
    tier: str
    count: int
    avg_trust_score: Optional[float]
    avg_p_top: Optional[float]
    last_updated: Optional[str]


class TrustSummaryResponse(BaseModel):
    tiers: List[TierStats]
    total_servers: int
    generated_at: str


router_responses = {200: {"model": TrustSummaryResponse}}


def get_router():
    from fastapi import APIRouter
    router = APIRouter(prefix="/api", tags=["trust"])

    @router.get("/trust/summary", response_model=TrustSummaryResponse)
    def get_trust_summary(session: Session = Depends(get_session)):
        sql = text("""
            SELECT
                r.risk_tier as tier,
                COUNT(DISTINCT r.server_id) as count,
                AVG(r.trust_score) as avg_trust_score,
                AVG(s.p_top) as avg_p_top,
                MAX(s.scored_at) as last_updated
            FROM mcp_server_registry r
            LEFT JOIN mcp_llm_axis_scores s ON r.server_id = s.server_id
                AND s.axis_name = 'overall_risk'
            GROUP BY r.risk_tier
            ORDER BY r.risk_tier
        """)
        result = session.execute(sql)
        rows = result.fetchall()

        tiers = []
        total_servers = 0
        for row in rows:
            tiers.append(TierStats(
                tier=str(row.tier) if row.tier else "unknown",
                count=row.count or 0,
                avg_trust_score=float(row.avg_trust_score) if row.avg_trust_score is not None else None,
                avg_p_top=float(row.avg_p_top) if row.avg_p_top is not None else None,
                last_updated=str(row.last_updated) if row.last_updated else None
            ))
            total_servers += row.count or 0

        return TrustSummaryResponse(
            tiers=tiers,
            total_servers=total_servers,
            generated_at=datetime.now(timezone.utc).isoformat()
        )

    return router


def create_app() -> FastAPI:
    app = FastAPI(title="Trust Summary Service")
    app.include_router(get_router())
    return app


def run_self_test():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                description TEXT,
                registry_source TEXT,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                scan_count INTEGER,
                meta TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                label TEXT,
                label_index INTEGER,
                model_version TEXT,
                decision_rule_version TEXT,
                adapter_sha256 TEXT,
                scored_at TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
            )
        """))

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    session = TestingSessionLocal()

    servers = [
        ("srv1", "Server One", "tier1", 0.85, "overall_risk", 0.15),
        ("srv2", "Server Two", "tier1", 0.90, "overall_risk", 0.10),
        ("srv3", "Server Three", "tier2", 0.65, "overall_risk", 0.45),
        ("srv4", "Server Four", "tier2", 0.55, "overall_risk", 0.55),
        ("srv5", "Server Five", "tier3", 0.25, "overall_risk", 0.85),
    ]

    now = datetime.now(timezone.utc).isoformat()
    for server_id, name, risk_tier, trust_score, axis_name, p_top in servers:
        session.execute(
            text("INSERT INTO mcp_server_registry VALUES (:s, :n, NULL, :rt, :ts, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 0, NULL)"),
            {"s": server_id, "n": name, "rt": risk_tier, "ts": trust_score}
        )
        session.execute(
            text("INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, p_danger, probs, label, label_index, model_version, decision_rule_version, adapter_sha256, scored_at, escalated, escalated_to) VALUES (:s, :a, :pt, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, :t, NULL, NULL)"),
            {"s": server_id, "a": axis_name, "pt": p_top, "t": now}
        )
    session.commit()
    session.close()

    that_app = create_app()
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/trust/summary")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    tier_map = {t["tier"]: t for t in data["tiers"]}

    assert tier_map["tier1"]["count"] == 2, f"tier1 count: {tier_map['tier1']['count']}"
    assert tier_map["tier2"]["count"] == 2, f"tier2 count: {tier_map['tier2']['count']}"
    assert tier_map["tier3"]["count"] == 1, f"tier3 count: {tier_map['tier3']['count']}"
    assert data["total_servers"] == 5

    print("PASS")


if __name__ == "__main__":
    run_self_test()