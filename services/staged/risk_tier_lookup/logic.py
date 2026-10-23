from __future__ import annotations

from typing import Optional

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session


class RiskTierResponse(BaseModel):
    server_id: str
    risk_tier: str
    composite_score: float
    dominant_axis: Optional[str]
    criteria_version: str = Field(default="1.0.0")


def get_risk_tier(
    server_id: str,
    session: Session = Depends(get_session),
) -> RiskTierResponse:
    """
    Compute risk tier for a server by analyzing LLM axis scores.

    Logic:
    - Any axis with p_critical >= 0.6 forces HIGH_RISK_ISOLATED
    - Otherwise, aggregate p_top values per axis into composite score
    - Map composite to tier thresholds
    """
    criteria_version = "1.0.0"

    query = text("""
        SELECT 
            s.server_id,
            a.axis_name,
            a.p_critical,
            a.p_top,
            a.label
        FROM mcp_llm_axis_scores a
        INNER JOIN mcp_server_registry s ON a.server_id = s.server_id
        WHERE a.server_id = :server_id
    """)

    result = session.execute(query, {"server_id": server_id})
    rows = result.fetchall()

    if not rows:
        return RiskTierResponse(
            server_id=server_id,
            risk_tier="UNKNOWN",
            composite_score=0.0,
            dominant_axis=None,
            criteria_version=criteria_version,
        )

    max_p_critical = 0.0
    axis_scores: dict[str, float] = {}
    dominant_axis_name: Optional[str] = None
    max_p_top = 0.0

    for row in rows:
        row_server_id = row[0]
        axis_name = row[1]
        p_critical = float(row[2]) if row[2] is not None else 0.0
        p_top = float(row[3]) if row[3] is not None else 0.0
        label = row[4]

        if p_critical >= 0.6:
            max_p_critical = max(max_p_critical, p_critical)

        if axis_name not in axis_scores:
            axis_scores[axis_name] = p_top
        else:
            axis_scores[axis_name] = max(axis_scores[axis_name], p_top)

        if p_top > max_p_top:
            max_p_top = p_top
            dominant_axis_name = axis_name

    if max_p_critical >= 0.6:
        return RiskTierResponse(
            server_id=server_id,
            risk_tier="HIGH_RISK_ISOLATED",
            composite_score=0.0,
            dominant_axis=dominant_axis_name,
            criteria_version=criteria_version,
        )

    composite_score = sum(axis_scores.values()) / len(axis_scores) if axis_scores else 0.0

    risk_tier = map_composite_to_tier(composite_score)

    return RiskTierResponse(
        server_id=server_id,
        risk_tier=risk_tier,
        composite_score=round(composite_score, 2),
        dominant_axis=dominant_axis_name,
        criteria_version=criteria_version,
    )


def map_composite_to_tier(composite: float) -> str:
    """Map composite score to risk tier."""
    if composite >= 75:
        return "TRUSTED_GENERAL"
    elif composite >= 60:
        return "TRUSTED_RESEARCH"
    elif composite >= 45:
        return "ENTERPRISE_CONTROLLED"
    elif composite >= 30:
        return "CAUTION_LIMITED"
    elif composite >= 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool

    # In-memory SQLite for self-test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create tables matching the schema
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                url TEXT,
                description TEXT,
                registry_source TEXT,
                risk_tier TEXT,
                trust_score REAL,
                confidence REAL,
                verdict TEXT,
                verdict_reasoning TEXT,
                first_seen TEXT,
                last_seen TEXT,
                last_scanned TEXT,
                last_assessed TEXT,
                meta TEXT,
                scan_count INTEGER
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT,
                axis_name TEXT,
                label TEXT,
                label_index INTEGER,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                probs TEXT,
                model_version TEXT,
                decision_rule_version TEXT,
                adapter_sha256 TEXT,
                scored_at TEXT,
                escalated INTEGER,
                escalated_to TEXT,
                FOREIGN KEY (server_id) REFERENCES mcp_server_registry(server_id)
            )
        """))
        conn.commit()

    # Seed test data
    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        # Seed server 1: normal server
        session.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, risk_tier)
            VALUES ('srv-001', 'normal-server', 'UNKNOWN')
        """))
        session.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical)
            VALUES ('srv-001', 'security', 'safe', 80.0, 0.1)
        """))
        session.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical)
            VALUES ('srv-001', 'reliability', 'stable', 75.0, 0.05)
        """))

        # Seed server 2: critical axis (p_critical >= 0.6)
        session.execute(text("""
            INSERT INTO mcp_server_registry (server_id, name, risk_tier)
            VALUES ('srv-002', 'critical-server', 'UNKNOWN')
        """))
        session.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical)
            VALUES ('srv-002', 'security', 'critical', 90.0, 0.75)
        """))
        session.execute(text("""
            INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical)
            VALUES ('srv-002', 'reliability', 'stable', 70.0, 0.1)
        """))

        session.commit()

    # Create FastAPI app for test
    test_app = FastAPI()

    @test_app.get("/api/servers/{server_id}/risk-tier")
    def test_endpoint(server_id: str, session: Session = Depends(lambda: SessionLocal())):
        return get_risk_tier(server_id, session)

    # Run tests using FastAPI TestClient
    from fastapi.testclient import TestClient

    client = TestClient(test_app)

    # Test critical server
    response1 = client.get("/api/servers/srv-002/risk-tier")
    assert response1.status_code == 200, f"Expected 200 for srv-002, got {response1.status_code}"
    data1 = response1.json()
    assert data1["risk_tier"] == "HIGH_RISK_ISOLATED", \
        f"Expected HIGH_RISK_ISOLATED for srv-002, got {data1['risk_tier']}"
    assert data1["server_id"] == "srv-002"

    # Test normal server
    response2 = client.get("/api/servers/srv-001/risk-tier")
    assert response2.status_code == 200, f"Expected 200 for srv-001, got {response2.status_code}"
    data2 = response2.json()
    assert data2["risk_tier"] in ["TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
                                   "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "KNOWN_THREAT"], \
        f"Unexpected tier for srv-001: {data2['risk_tier']}"
    assert data2["server_id"] == "srv-001"

    print("PASS")
    sys.exit(0)