from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy.sql import Row
from app.models import MCPLLMAxisScores, MCPServerRegistry
from enum import Enum

class RiskTier(str, Enum):
    TRUSTED_GENERAL = "TRUSTED_GENERAL"
    TRUSTED_RESEARCH = "TRUSTED_RESEARCH"
    ENTERPRISE_CONTROLLED = "ENTERPRISE_CONTROLLED"
    CAUTION_LIMITED = "CAUTION_LIMITED"
    HIGH_RISK_ISOLATED = "HIGH_RISK_ISOLATED"
    KNOWN_THREAT = "KNOWN_THREAT"

class AxisLabel(str, Enum):
    CRITICAL = "critical"
    DANGER = "danger"
    WARNING = "warning"
    CAUTION = "caution"
    LOW = "low"
    NONE = "none"

def get_latest_scores_for_server(session: Session, server_id: str) -> List[Row]:
    return session.execute(
        session.query(MCPLLMAxisScores)
        .filter(MCPLLMAxisScores.server_id == server_id)
        .order_by(MCPLLMAxisScores.scored_at.desc())
        .limit(7)
    ).scalars().all()

def derive_risk_tier_from_axes(
    session: Session,
    server_id: str,
    override_rules: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    scores = get_latest_scores_for_server(session, server_id)
    if not scores:
        raise ValueError(f"No scores found for server_id: {server_id}")

    axes = {}
    critical_axis_found = False
    for score in scores:
        axis_name = score.axis_name
        label = score.label
        label_index = AxisLabel[label].value if label in AxisLabel._value2member_map_ else None
        p_top = score.p_top
        p_critical = score.p_critical
        p_danger = score.p_danger
        probs = score.probs
        escalated = score.escalated

        axes[axis_name] = {
            "label": label,
            "label_index": label_index,
            "p_top": p_top,
            "p_critical": p_critical,
            "p_danger": p_danger,
            "probs": probs,
            "escalated": escalated
        }

        if label == "critical":
            critical_axis_found = True

    overall_score = scores[0].p_top if scores else 0.0
    overall_label = scores[0].label if scores else "none"

    if critical_axis_found:
        risk_tier = RiskTier.HIGH_RISK_ISOLATED
    else:
        if overall_score > 75:
            risk_tier = RiskTier.TRUSTED_GENERAL
        elif overall_score > 60:
            risk_tier = RiskTier.TRUSTED_RESEARCH
        elif overall_score > 45:
            risk_tier = RiskTier.ENTERPRISE_CONTROLLED
        elif overall_score > 30:
            risk_tier = RiskTier.CAUTION_LIMITED
        elif overall_score > 15:
            risk_tier = RiskTier.HIGH_RISK_ISOLATED
        else:
            risk_tier = RiskTier.KNOWN_THREAT

    if override_rules and override_rules.get("trust_gate") == "trusted":
        risk_tier = RiskTier.TRUSTED_GENERAL

    return {
        "server_id": server_id,
        "axes": axes,
        "overall_score": overall_score,
        "overall_label": overall_label,
        "risk_tier": risk_tier.value,
        "criteria_version": "1.0",
        "scored_at": datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    test_session = SessionLocal()
    test_server_id = "test_server_123"

    test_scores = [
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="overall_risk",
            label="warning",
            p_top=65.0,
            p_critical=0.1,
            p_danger=0.2,
            probs="[0.1, 0.2, 0.3, 0.4]",
            escalated=False,
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="auth_strength",
            label="caution",
            p_top=50.0,
            p_critical=0.1,
            p_danger=0.2,
            probs="[0.1, 0.2, 0.3, 0.4]",
            escalated=False,
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="capability_breadth",
            label="low",
            p_top=40.0,
            p_critical=0.1,
            p_danger=0.2,
            probs="[0.1, 0.2, 0.3, 0.4]",
            escalated=False,
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="data_sensitivity",
            label="none",
            p_top=30.0,
            p_critical=0.1,
            p_danger=0.2,
            probs="[0.1, 0.2, 0.3, 0.4]",
            escalated=False,
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="network_egress",
            label="warning",
            p_top=55.0,
            p_critical=0.1,
            p_danger=0.2,
            probs="[0.1, 0.2, 0.3, 0.4]",
            escalated=False,
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="maintainer_trust",
            label="caution",
            p_top=45.0,
            p_critical=0.1,
            p_danger=0.2,
            probs="[0.1, 0.2, 0.3, 0.4]",
            escalated=False,
            scored_at=datetime.utcnow()
        ),
        MCPLLMAxisScores(
            server_id=test_server_id,
            axis_name="exploit_surface",
            label="low",
            p_top=35.0,
            p_critical=0.1,
            p_danger=0.2,
            probs="[0.1, 0.2, 0.3, 0.4]",
            escalated=False,
            scored_at=datetime.utcnow()
        )
    ]

    test_session.add_all(test_scores)
    test_session.commit()

    test_server = MCPServerRegistry(
        server_id=test_server_id,
        hostname="test.example.com",
        ip_address="192.168.1.1",
        org_id="test_org",
        trust_gate="untrusted"
    )
    test_session.add(test_server)
    test_session.commit()

    result = derive_risk_tier_from_axes(test_session, test_server_id)
    assert result["risk_tier"] in [tier.value for tier in RiskTier]
    assert len(result["axes"]) == 7
    assert result["scored_at"].endswith("+00:00")

    print("PASS")