from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import List, Optional
from enum import Enum
from app.db import get_session
from app.models import MCPPolicyRule
from sqlalchemy.orm import Session
from sqlalchemy import and_
import httpx

router = APIRouter(prefix="/policy-rules", tags=["policy-rules"])

class VerdictAction(str, Enum):
    APPROVE = "APPROVE"
    CONDITIONAL = "CONDITIONAL"
    REJECT = "REJECT"
    OVERRIDE = "OVERRIDE"

class MCPPolicyRuleCreate(BaseModel):
    rule_type: str
    pattern: str
    verdict_action: VerdictAction
    description: Optional[str] = None
    priority: int = Field(default=100, ge=0, le=1000)

class MCPPolicyRuleResponse(MCPPolicyRuleCreate):
    id: int
    org_id: int

class MCPPolicyRuleListResponse(BaseModel):
    rules: List[MCPPolicyRuleResponse]
    total: int

def get_current_org_id(session: Session = Depends(get_session)):
    # In a real implementation, this would extract org_id from JWT
    # For this example, we'll assume it's passed via session
    return 1  # Default org_id for testing

@router.get("/", response_model=MCPPolicyRuleListResponse)
async def list_policy_rules(
    org_id: int = Depends(get_current_org_id),
    session: Session = Depends(get_session),
    skip: int = 0,
    limit: int = 100,
    rule_type: Optional[str] = None,
    pattern: Optional[str] = None,
    verdict_action: Optional[VerdictAction] = None
):
    query = session.query(MCPPolicyRule).filter(MCPPolicyRule.org_id == org_id)

    if rule_type:
        query = query.filter(MCPPolicyRule.rule_type == rule_type)
    if pattern:
        query = query.filter(MCPPolicyRule.pattern.like(pattern))
    if verdict_action:
        query = query.filter(MCPPolicyRule.verdict_action == verdict_action)

    total = query.count()
    rules = query.offset(skip).limit(limit).all()

    return {
        "rules": rules,
        "total": total
    }

@router.post("/", response_model=MCPPolicyRuleResponse, status_code=status.HTTP_201_CREATED)
async def create_policy_rule(
    rule: MCPPolicyRuleCreate,
    org_id: int = Depends(get_current_org_id),
    session: Session = Depends(get_session)
):
    # Check if rule already exists (idempotent create)
    existing_rule = session.query(MCPPolicyRule).filter(
        and_(
            MCPPolicyRule.org_id == org_id,
            MCPPolicyRule.pattern == rule.pattern
        )
    ).first()

    if existing_rule:
        # Update existing rule
        existing_rule.rule_type = rule.rule_type
        existing_rule.verdict_action = rule.verdict_action
        existing_rule.description = rule.description
        existing_rule.priority = rule.priority
        session.commit()
        return existing_rule

    # Create new rule
    new_rule = MCPPolicyRule(
        org_id=org_id,
        rule_type=rule.rule_type,
        pattern=rule.pattern,
        verdict_action=rule.verdict_action,
        description=rule.description,
        priority=rule.priority
    )
    session.add(new_rule)
    session.commit()
    session.refresh(new_rule)
    return new_rule

@router.get("/{rule_id}", response_model=MCPPolicyRuleResponse)
async def get_policy_rule(
    rule_id: int,
    org_id: int = Depends(get_current_org_id),
    session: Session = Depends(get_session)
):
    rule = session.query(MCPPolicyRule).filter(
        and_(
            MCPPolicyRule.id == rule_id,
            MCPPolicyRule.org_id == org_id
        )
    ).first()

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule

@router.put("/{rule_id}", response_model=MCPPolicyRuleResponse)
async def update_policy_rule(
    rule_id: int,
    rule: MCPPolicyRuleCreate,
    org_id: int = Depends(get_current_org_id),
    session: Session = Depends(get_session)
):
    existing_rule = session.query(MCPPolicyRule).filter(
        and_(
            MCPPolicyRule.id == rule_id,
            MCPPolicyRule.org_id == org_id
        )
    ).first()

    if not existing_rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    existing_rule.rule_type = rule.rule_type
    existing_rule.pattern = rule.pattern
    existing_rule.verdict_action = rule.verdict_action
    existing_rule.description = rule.description
    existing_rule.priority = rule.priority

    session.commit()
    return existing_rule

@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy_rule(
    rule_id: int,
    org_id: int = Depends(get_current_org_id),
    session: Session = Depends(get_session)
):
    rule = session.query(MCPPolicyRule).filter(
        and_(
            MCPPolicyRule.id == rule_id,
            MCPPolicyRule.org_id == org_id
        )
    ).first()

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    session.delete(rule)
    session.commit()

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPPolicyRule
    from sqlalchemy.orm import sessionmaker

    # Create test app
    app = FastAPI()
    app.include_router(router)

    # Override dependencies for testing
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create test database
    Base.metadata.create_all(engine)

    # Test client
    client = TestClient(app)

    # Test data
    test_rule = {
        "rule_type": "test_type",
        "pattern": "test_pattern",
        "verdict_action": "APPROVE",
        "description": "Test description",
        "priority": 100
    }

    # Test POST then GET
    response = client.post("/", json=test_rule)
    assert response.status_code == 201
    created_rule = response.json()

    response = client.get(f"/{created_rule['id']}")
    assert response.status_code == 200
    assert response.json() == created_rule

    # Test PUT
    updated_rule = {
        "rule_type": "updated_type",
        "pattern": "updated_pattern",
        "verdict_action": "REJECT",
        "description": "Updated description",
        "priority": 200
    }
    response = client.put(f"/{created_rule['id']}", json=updated_rule)
    assert response.status_code == 200
    assert response.json()["rule_type"] == "updated_type"

    # Test DELETE
    response = client.delete(f"/{created_rule['id']}")
    assert response.status_code == 204

    print("PASS")