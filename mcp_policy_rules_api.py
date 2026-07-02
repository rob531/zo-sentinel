from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpPolicyRule

router = APIRouter()

class PolicyRuleResponse(BaseModel):
    rule_id: str
    rule_type: str
    pattern: str
    description: str

@router.get("/mcp/policy_rules", response_model=List[PolicyRuleResponse])
def list_policy_rules(
    rule_type: Optional[str] = None,
    pattern: Optional[str] = None,
    db: Session = Depends(get_session)
):
    query = db.query(McpPolicyRule)
    if rule_type:
        query = query.filter(McpPolicyRule.rule_type == rule_type)
    if pattern:
        query = query.filter(McpPolicyRule.pattern == pattern)
    policy_rules = query.all()
    if not policy_rules:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No policy rules found"
        )
    return policy_rules

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    # Create a SQLite in-memory database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    # Override the get_session dependency to use the test database
    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_session] = override_get_session
    
    # Create the tables in the test database
    Base.metadata.create_all(bind=engine)
    
    # Create a test client
    client = TestClient(app)
    
    # Test the /mcp/policy_rules endpoint
    response = client.get("/mcp/policy_rules")
    assert response.status_code == status.HTTP_200_OK
    assert isinstance(response.json(), list)
    
    # Test filtering by rule_type
    response = client.get("/mcp/policy_rules?rule_type=type1")
    assert response.status_code == status.HTTP_200_OK
    
    # Test filtering by pattern
    response = client.get("/mcp/policy_rules?pattern=pattern1")
    assert response.status_code == status.HTTP_200_OK
    
    # Test 404 response
    response = client.get("/mcp/policy_rules?rule_type=nonexistent")
    assert response.status_code == status.HTTP_404_NOT_FOUND
    
    print("PASS")