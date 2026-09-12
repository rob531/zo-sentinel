from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class TierSnapshot(BaseModel):
    tier: str
    count: int
    server_ids: List[int]


class RiskTierSnapshotResponse(BaseModel):
    generated_at: str
    total_servers: int
    tiers: List[TierSnapshot]


router = APIRouter()


@router.get("/risk/snapshot", response_model=RiskTierSnapshotResponse)
def get_risk_snapshot(db: Session = Depends(get_session)) -> RiskTierSnapshotResponse:
    tiers_order = [
        "TRUSTED_GENERAL",
        "TRUSTED_RESEARCH",
        "ENTERPRISE_CONTROLLED",
        "CAUTION_LIMITED",
        "HIGH_RISK_ISOLATED",
        "KNOWN_THREAT",
        "INSUFFICIENT",
    ]
    
    result = (
        db.query(
            McpServerRegistry.risk_tier,
            McpServerRegistry.server_id,
        )
        .join(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .distinct()
        .all()
    )
    
    tier_data = {tier: {"count": 0, "server_ids": []} for tier in tiers_order}
    total_servers = 0
    
    for row in result:
        tier = row.risk_tier or "INSUFFICIENT"
        if tier in tier_data:
            tier_data[tier]["count"] += 1
            tier_data[tier]["server_ids"].append(row.server_id)
            total_servers += 1
    
    tiers = [
        TierSnapshot(
            tier=tier,
            count=tier_data[tier]["count"],
            server_ids=tier_data[tier]["server_ids"],
        )
        for tier in tiers_order
    ]
    
    return RiskTierSnapshotResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        total_servers=total_servers,
        tiers=tiers,
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE McpServerRegistry (
                    server_id INTEGER PRIMARY KEY,
                    risk_tier TEXT,
                    name TEXT,
                    url TEXT,
                    registry_source TEXT,
                    description TEXT,
                    verdict TEXT,
                    verdict_reasoning TEXT,
                    trust_score REAL,
                    confidence REAL,
                    first_seen TEXT,
                    last_seen TEXT,
                    last_scanned TEXT,
                    last_assessed TEXT,
                    scan_count INTEGER,
                    meta TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE McpLlmAxisScore (
                    id INTEGER PRIMARY KEY,
                    server_id INTEGER,
                    adapter_sha256 TEXT,
                    model_version TEXT,
                    decision_rule_version TEXT,
                    axis_name TEXT,
                    label TEXT,
                    label_index INTEGER,
                    p_critical REAL,
                    p_danger REAL,
                    p_top REAL,
                    probs TEXT,
                    scored_at TEXT,
                    escalated INTEGER,
                    escalated_to TEXT
                )
                """
            )
        )

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    session = TestingSessionLocal()
    session.add_all([
        McpServerRegistry(server_id=1, risk_tier="TRUSTED_GENERAL", name="s1"),
        McpServerRegistry(server_id=2, risk_tier="TRUSTED_GENERAL", name="s2"),
        McpServerRegistry(server_id=3, risk_tier="CAUTION_LIMITED", name="s3"),
        McpServerRegistry(server_id=4, risk_tier="CAUTION_LIMITED", name="s4"),
        McpServerRegistry(server_id=5, risk_tier="HIGH_RISK_ISOLATED", name="s5"),
        McpServerRegistry(server_id=6, risk_tier="INSUFFICIENT", name="s6"),
    ])
    session.add_all([
        McpLlmAxisScore(server_id=1, axis_name="x", label="a"),
        McpLlmAxisScore(server_id=2, axis_name="x", label="a"),
        McpLlmAxisScore(server_id=3, axis_name="x", label="a"),
        McpLlmAxisScore(server_id=4, axis_name="x", label="a"),
        McpLlmAxisScore(server_id=5, axis_name="x", label="a"),
        McpLlmAxisScore(server_id=6, axis_name="x", label="a"),
    ])
    session.commit()
    session.close()

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/risk/snapshot")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["total_servers"] == 6, f"Expected total_servers=6, got {data['total_servers']}"

    non_zero_tiers = [t for t in data["tiers"] if t["count"] > 0]
    assert len(non_zero_tiers) >= 3, f"Expected at least 3 non-zero tier counts, got {len(non_zero_tiers)}"

    print("PASS")