from datetime import datetime
from typing import Dict, List
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

def calculate_risk_tier(server_id: int, session: Session) -> str:
    """Calculate risk tier based on axis scores for a given server."""
    scores = session.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()

    if not scores:
        return "TRUSTED_GENERAL"

    axis_totals = {
        "HIGH_RISK_ISOLATED": 0,
        "CAUTION_LIMITED": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "TRUSTED_RESEARCH": 0,
        "TRUSTED_GENERAL": 0
    }

    for score in scores:
        if score.axis_name == "risk":
            if score.p_top > 0.8:
                axis_totals["HIGH_RISK_ISOLATED"] += 1
            elif score.p_top > 0.6:
                axis_totals["CAUTION_LIMITED"] += 1
            elif score.p_top > 0.4:
                axis_totals["ENTERPRISE_CONTROLLED"] += 1
            elif score.p_top > 0.2:
                axis_totals["TRUSTED_RESEARCH"] += 1
            else:
                axis_totals["TRUSTED_GENERAL"] += 1

    max_axis = max(axis_totals, key=axis_totals.get)
    return max_axis

def generate_report(date: str) -> Dict[str, Dict[str, int]]:
    """Generate a daily summary report of risk tier distribution across all MCP servers."""
    session = get_session()

    servers = session.query(McpServerRegistry).all()
    tier_counts = {
        "HIGH_RISK_ISOLATED": 0,
        "CAUTION_LIMITED": 0,
        "ENTERPRISE_CONTROLLED": 0,
        "TRUSTED_RESEARCH": 0,
        "TRUSTED_GENERAL": 0
    }

    for server in servers:
        tier = calculate_risk_tier(server.server_id, session)
        tier_counts[tier] += 1

    session.close()

    return {
        "date": date,
        "tier_counts": tier_counts
    }

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.dependency_overrides import dependency_overrides

    # Create in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the session dependency for testing
    dependency_overrides[get_session] = lambda: SessionLocal()

    # Add sample data
    session = SessionLocal()
    sample_servers = [
        McpServerRegistry(server_id=1, risk_tier="HIGH_RISK_ISOLATED"),
        McpServerRegistry(server_id=2, risk_tier="CAUTION_LIMITED"),
        McpServerRegistry(server_id=3, risk_tier="ENTERPRISE_CONTROLLED"),
        McpServerRegistry(server_id=4, risk_tier="TRUSTED_RESEARCH"),
        McpServerRegistry(server_id=5, risk_tier="TRUSTED_GENERAL")
    ]
    session.add_all(sample_servers)

    sample_scores = [
        McpLlmAxisScore(server_id=1, axis_name="risk", p_top=0.9),
        McpLlmAxisScore(server_id=2, axis_name="risk", p_top=0.7),
        McpLlmAxisScore(server_id=3, axis_name="risk", p_top=0.5),
        McpLlmAxisScore(server_id=4, axis_name="risk", p_top=0.3),
        McpLlmAxisScore(server_id=5, axis_name="risk", p_top=0.1)
    ]
    session.add_all(sample_scores)
    session.commit()

    # Generate report and assert expected counts
    today = datetime.now().strftime("%Y-%m-%d")
    report = generate_report(today)
    expected_counts = {
        "HIGH_RISK_ISOLATED": 1,
        "CAUTION_LIMITED": 1,
        "ENTERPRISE_CONTROLLED": 1,
        "TRUSTED_RESEARCH": 1,
        "TRUSTED_GENERAL": 1
    }
    assert report["tier_counts"] == expected_counts

    print("PASS")