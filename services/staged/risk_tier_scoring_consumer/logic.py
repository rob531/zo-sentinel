from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from pydantic import BaseModel
from write_service import execute

class RiskTierScoringResult(BaseModel):
    servers_processed: int
    tiers: Dict[str, int]

def compute_risk_tier(server_id: int, db: Session) -> Optional[str]:
    # Get all axis scores for the server
    axis_scores = db.query(McpLlmAxisScore).filter(McpLlmAxisScore.server_id == server_id).all()

    # Check if we have all 7 axis scores
    if len(axis_scores) != 7:
        return None

    # Check for any CRITICAL escalation
    for score in axis_scores:
        if score.escalated == "CRITICAL":
            return "HIGH_RISK_ISOLATED"

    # Compute composite p_top
    composite_p_top = sum(score.p_top for score in axis_scores) / 7

    # Apply thresholds
    if composite_p_top > 75:
        return "TRUSTED_GENERAL"
    elif composite_p_top > 60:
        return "TRUSTED_RESEARCH"
    elif composite_p_top > 45:
        return "ENTERPRISE_CONTROLLED"
    elif composite_p_top > 30:
        return "CAUTION_LIMITED"
    elif composite_p_top > 15:
        return "HIGH_RISK_ISOLATED"
    else:
        return "KNOWN_THREAT"

def process_all_servers(db: Session) -> RiskTierScoringResult:
    # Get all servers with at least one axis score
    servers_with_scores = db.query(McpLlmAxisScore.server_id).distinct().all()
    servers_with_scores = [server[0] for server in servers_with_scores]

    result = RiskTierScoringResult(servers_processed=0, tiers={
        "TRUSTED_GENERAL": 0,
        "TRUSTED_RESEARCH": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "CAUTION_LIMITED": 0,
        "HIGH_RISK_ISOLATED": 0,
        "KNOWN_THREAT": 0
    })

    for server_id in servers_with_scores:
        risk_tier = compute_risk_tier(server_id, db)
        if risk_tier:
            result.servers_processed += 1
            result.tiers[risk_tier] += 1

            # Update server registry
            server_registry = db.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
            if server_registry:
                server_registry.risk_tier = risk_tier
            else:
                db.add(McpServerRegistry(server_id=server_id, risk_tier=risk_tier))

    db.commit()
    return result

def get_last_run_summary(db: Session) -> RiskTierScoringResult:
    # Get all servers with risk tier
    servers = db.query(McpServerRegistry).all()

    result = RiskTierScoringResult(servers_processed=len(servers), tiers={
        "TRUSTED_GENERAL": 0,
        "TRUSTED_RESEARCH": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "CAUTION_LIMITED": 0,
        "HIGH_RISK_ISOLATED": 0,
        "KNOWN_THREAT": 0
    })

    for server in servers:
        result.tiers[server.risk_tier] += 1

    return result

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    execute("""
    INSERT INTO mcp_server_registry (server_id, risk_tier) VALUES
    (1, NULL),
    (2, NULL),
    (3, NULL),
    (4, NULL),
    (5, NULL);

    INSERT INTO mcp_llm_axis_scores (server_id, axis_name, p_top, p_critical, p_danger, escalated, label) VALUES
    (1, 'axis1', 80, 10, 5, 'NONE', 'label1'),
    (1, 'axis2', 85, 5, 5, 'NONE', 'label2'),
    (1, 'axis3', 90, 2, 3, 'NONE', 'label3'),
    (1, 'axis4', 75, 8, 4, 'NONE', 'label4'),
    (1, 'axis5', 82, 6, 4, 'NONE', 'label5'),
    (1, 'axis6', 78, 7, 3, 'NONE', 'label6'),
    (1, 'axis7', 88, 4, 3, 'NONE', 'label7'),
    (2, 'axis1', 50, 20, 15, 'CRITICAL', 'label1'),
    (2, 'axis2', 45, 25, 18, 'NONE', 'label2'),
    (2, 'axis3', 40, 30, 20, 'NONE', 'label3'),
    (2, 'axis4', 55, 18, 12, 'NONE', 'label4'),
    (2, 'axis5', 60, 15, 10, 'NONE', 'label5'),
    (2, 'axis6', 52, 17, 11, 'NONE', 'label6'),
    (2, 'axis7', 48, 19, 13, 'NONE', 'label7'),
    (3, 'axis1', 30, 30, 20, 'NONE', 'label1'),
    (3, 'axis2', 25, 35, 25, 'NONE', 'label2'),
    (4, 'axis1', 20, 40, 30, 'NONE', 'label1'),
    (5, 'axis1', 10, 50, 40, 'NONE', 'label1');
    """)

    # Process servers
    db = next(override_get_session())
    result = process_all_servers(db)

    # Verify results
    assert result.servers_processed >= 2
    assert result.tiers["HIGH_RISK_ISOLATED"] == 1

    # Get status
    status = get_last_run_summary(db)
    assert status.servers_processed >= 2
    assert status.tiers["HIGH_RISK_ISOLATED"] == 1

    print("PASS")