import logging
from typing import Dict, Any
from datetime import datetime
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpLlmAxisScores
import requests

logger = logging.getLogger(__name__)

def trust_gate(url: str, name: str, axis_labels: Dict[str, str]) -> Dict[str, Any]:
    try:
        response = requests.post(
            url,
            json={"name": name, "axis_labels": axis_labels},
            timeout=5
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Trust gate call failed: {e}")
        return {"published_overall_risk": False, "trusted": False}

def get_server_verdict(server_id: str, db_session: Session = Depends(get_session)) -> Dict[str, Any]:
    axes = ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
            "network_egress", "maintainer_trust", "exploit_surface"]

    axis_data = {}
    for axis in axes:
        row = db_session.query(McpLlmAxisScores).filter(
            McpLlmAxisScores.server_id == server_id,
            McpLlmAxisScores.axis_name == axis
        ).first()

        if row:
            axis_data[axis] = {
                "label": row.label,
                "p_top": float(row.p_top),
                "p_critical": float(row.p_critical),
                "escalated": bool(row.escalated)
            }

    if not axis_data:
        return {
            "server_id": server_id,
            "axes": {},
            "overall_risk": {"label": "", "p_top": 0.0},
            "risk_tier": "INSUFFICIENT",
            "criteria_version": "",
            "scored_at": ""
        }

    overall_risk = axis_data.get("overall_risk", {})
    criteria_version = overall_risk.get("label", "")
    scored_at = datetime.now().isoformat() if not axis_data.get("overall_risk") else axis_data["overall_risk"]["scored_at"]

    axis_labels = {axis: data["label"] for axis, data in axis_data.items()}
    trust_gate_result = trust_gate("http://127.0.0.1:8772/trust_gate", server_id, axis_labels)

    critical_axes = [axis for axis, data in axis_data.items() if data["label"] == "CRITICAL"]
    if critical_axes:
        logger.info(f"Critical label detected on axes: {critical_axes}. Forcing risk tier override.")
        risk_tier = "HIGH_RISK_ISOLATED"
    else:
        if trust_gate_result.get("trusted", False):
            risk_tier = "TRUSTED_GENERAL" if trust_gate_result.get("published_overall_risk", False) else "TRUSTED_RESEARCH"
        else:
            risk_tier = "ENTERPRISE_CONTROLLED" if overall_risk.get("label", "") == "HIGH" else "CAUTION_LIMITED"

    return {
        "server_id": server_id,
        "axes": axis_data,
        "overall_risk": overall_risk,
        "risk_tier": risk_tier,
        "criteria_version": criteria_version,
        "scored_at": scored_at
    }

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_session = TestSession()
    test_server_id = "test_server_123"

    for axis in ["overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
                 "network_egress", "maintainer_trust", "exploit_surface"]:
        test_session.add(McpLlmAxisScores(
            server_id=test_server_id,
            axis_name=axis,
            label="MEDIUM",
            label_index=2,
            p_top=0.7,
            p_critical=0.1,
            p_danger=0.05,
            escalated=False,
            decision_rule_version="v1.0",
            model_version="v1.0",
            scored_at=datetime.now().isoformat()
        ))

    test_session.commit()

    app.dependency_overrides[get_session] = lambda: test_session

    result = get_server_verdict(test_server_id)
    assert len(result["axes"]) == 7
    assert result["risk_tier"] in ["TRUSTED_GENERAL", "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED", "CAUTION_LIMITED", "HIGH_RISK_ISOLATED", "INSUFFICIENT"]
    assert result["scored_at"] is not None

    print("PASS")