from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, Org, User

router = APIRouter()

class AxisCriteria(BaseModel):
    name: str
    label: str
    description: str
    decision_rule_version: str
    model_version: str

class RiskTierThreshold(BaseModel):
    tier: int
    min_score: float
    max_score: float
    composite_condition: str

class ScoringAxisCriteriaResponse(BaseModel):
    axes: List[AxisCriteria]
    risk_tier_thresholds: List[RiskTierThreshold]
    criteria_version: str

@router.get("/scoring/axis-criteria", response_model=ScoringAxisCriteriaResponse)
async def get_scoring_axis_criteria() -> Dict[str, Any]:
    axes = [
        {
            "name": "financial_risk",
            "label": "Financial Risk",
            "description": "Assesses the financial stability and risk profile of the organization.",
            "decision_rule_version": "1.0",
            "model_version": "1.0"
        },
        {
            "name": "operational_risk",
            "label": "Operational Risk",
            "description": "Evaluates the operational efficiency and risk management practices.",
            "decision_rule_version": "1.0",
            "model_version": "1.0"
        },
        {
            "name": "compliance_risk",
            "label": "Compliance Risk",
            "description": "Measures adherence to regulatory and compliance requirements.",
            "decision_rule_version": "1.0",
            "model_version": "1.0"
        },
        {
            "name": "reputational_risk",
            "label": "Reputational Risk",
            "description": "Assesses the potential impact on the organization's reputation.",
            "decision_rule_version": "1.0",
            "model_version": "1.0"
        },
        {
            "name": "strategic_risk",
            "label": "Strategic Risk",
            "description": "Evaluates the strategic direction and associated risks.",
            "decision_rule_version": "1.0",
            "model_version": "1.0"
        },
        {
            "name": "legal_risk",
            "label": "Legal Risk",
            "description": "Assesses the legal exposure and potential liabilities.",
            "decision_rule_version": "1.0",
            "model_version": "1.0"
        },
        {
            "name": "cybersecurity_risk",
            "label": "Cybersecurity Risk",
            "description": "Evaluates the cybersecurity posture and risk exposure.",
            "decision_rule_version": "1.0",
            "model_version": "1.0"
        }
    ]

    risk_tier_thresholds = [
        {
            "tier": 1,
            "min_score": 0.0,
            "max_score": 0.2,
            "composite_condition": "Low risk"
        },
        {
            "tier": 2,
            "min_score": 0.21,
            "max_score": 0.4,
            "composite_condition": "Moderate risk"
        },
        {
            "tier": 3,
            "min_score": 0.41,
            "max_score": 0.6,
            "composite_condition": "High risk"
        },
        {
            "tier": 4,
            "min_score": 0.61,
            "max_score": 0.8,
            "composite_condition": "Very high risk"
        },
        {
            "tier": 5,
            "min_score": 0.81,
            "max_score": 1.0,
            "composite_condition": "Extreme risk"
        },
        {
            "tier": 6,
            "min_score": 0.0,
            "max_score": 1.0,
            "composite_condition": "Special condition"
        }
    ]

    return {
        "axes": axes,
        "risk_tier_thresholds": risk_tier_thresholds,
        "criteria_version": "1.0"
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router, prefix="/scoring")

    client = TestClient(app)

    response = client.get("/scoring/axis-criteria")
    assert response.status_code == 200
    response_data = response.json()

    assert len(response_data["axes"]) == 7
    assert len(response_data["risk_tier_thresholds"]) == 6

    print("PASS")