# services/staged/app_scoring_consumer_auth_strength/contract.py
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


router = APIRouter(prefix="/auth-strength", tags=["auth_strength"])


class AxisRiskOutput(BaseModel):
    server_id: str
    adapter_sha256: str
    axis_name: str
    label: str
    label_index: int
    p_critical: Optional[float]
    p_danger: Optional[float]
    p_top: Optional[float]
    probs: Optional[str]
    model_version: Optional[str]
    decision_rule_version: Optional[str]
    escalated: bool
    escalated_to: Optional[str]
    scored_at: datetime

    class Config:
        from_attributes = True


class AuthStrengthSummary(BaseModel):
    total_servers: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    unknown_risk_count: int
    risk_distribution: dict


@router.get("/servers/{server_id}/auth-strength", response_model=AxisRiskOutput)
def get_server_auth_strength(
    server_id: str,
    session: Session = Depends(get_session),
) -> AxisRiskOutput:
    stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .where(McpLlmAxisScore.axis_name == "auth_strength")
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    )
    result = session.execute(stmt).scalar_one_or_none()
    if result is None:
        raise ValueError(f"No auth_strength score found for server_id={server_id}")
    return AxisRiskOutput.model_validate(result)


@router.get("/summary", response_model=AuthStrengthSummary)
def get_auth_strength_summary(
    session: Session = Depends(get_session),
) -> AuthStrengthSummary:
    stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.axis_name == "auth_strength")
    )
    scores = session.execute(stmt).scalars().all()

    total = len(scores)
    high_risk = 0
    medium_risk = 0
    low_risk = 0
    unknown = 0

    for score in scores:
        if score.label is None:
            unknown += 1
        elif score.label_index is not None:
            if score.label_index >= 3:
                high_risk += 1
            elif score.label_index >= 1:
                medium_risk += 1
            else:
                low_risk += 1
        elif "danger" in str(score.label).lower() or "critical" in str(score.label).lower():
            high_risk += 1
        elif "warning" in str(score.label).lower():
            medium_risk += 1
        else:
            low_risk += 1

    return AuthStrengthSummary(
        total_servers=total,
        high_risk_count=high_risk,
        medium_risk_count=medium_risk,
        low_risk_count=low_risk,
        unknown_risk_count=unknown,
        risk_distribution={
            "high": high_risk,
            "medium": medium_risk,
            "low": low_risk,
            "unknown": unknown,
        },
    )


def get_mcp_llm_axis_scores(
    session: Session,
    axis_name: str = "auth_strength",
    server_id: Optional[str] = None,
    limit: int = 100,
) -> list[McpLlmAxisScore]:
    stmt = (
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.axis_name == axis_name)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(limit)
    )
    if server_id:
        stmt = stmt.where(McpLlmAxisScore.server_id == server_id)
    return list(session.execute(stmt).scalars().all())


def get_service_health(session: Session) -> dict:
    stmt = select(func.count(McpLlmAxisScore.id)).where(
        McpLlmAxisScore.axis_name == "auth_strength"
    )
    count = session.execute(stmt).scalar() or 0
    return {
        "service": "app_scoring_consumer_auth_strength",
        "status": "healthy" if count > 0 else "degraded",
        "axis_name": "auth_strength",
        "record_count": count,
    }


if __name__ == "__main__":
    import os
    import sys
    from pathlib import Path

    project_root = Path(__file__).parent.parent.parent.parent
    sys.path.insert(0, str(project_root))

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient

    client = TestClient(that_app)

    response = client.get("/auth-strength/summary")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert "total_servers" in data, f"Missing total_servers in response: {data}"
    assert "risk_distribution" in data, f"Missing risk_distribution in response: {data}"

    health_response = client.get("/auth-strength/summary")
    assert health_response.status_code == 200

    print("PASS")
    sys.exit(0)