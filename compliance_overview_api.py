from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from typing import List, Optional

# Database setup
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Pydantic models
class PolicyRule(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_active: bool

class Decision(BaseModel):
    id: int
    rule_id: int
    decision: str
    timestamp: str

class Exemption(BaseModel):
    id: int
    rule_id: int
    reason: str
    timestamp: str

class ComplianceOverview(BaseModel):
    active_policy_rules: List[PolicyRule]
    recent_decisions: List[Decision]
    current_exemptions: List[Exemption]

# FastAPI router
router = APIRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/compliance/overview", response_model=ComplianceOverview)
def get_compliance_overview(db=Depends(get_db)):
    # Query active policy rules
    active_policy_rules_query = text("""
        SELECT id, name, description, is_active
        FROM mcp_policy_rules
        WHERE is_active = TRUE
    """)
    active_policy_rules = db.execute(active_policy_rules_query).fetchall()

    # Query recent decisions
    recent_decisions_query = text("""
        SELECT id, rule_id, decision, timestamp
        FROM mcp_decisions
        ORDER BY timestamp DESC
        LIMIT 10
    """)
    recent_decisions = db.execute(recent_decisions_query).fetchall()

    # Query current exemptions
    current_exemptions_query = text("""
        SELECT id, rule_id, reason, timestamp
        FROM mcp_exemptions
        ORDER BY timestamp DESC
        LIMIT 10
    """)
    current_exemptions = db.execute(current_exemptions_query).fetchall()

    return {
        "active_policy_rules": [dict(rule) for rule in active_policy_rules],
        "recent_decisions": [dict(decision) for decision in recent_decisions],
        "current_exemptions": [dict(exemption) for exemption in current_exemptions]
    }

# Test setup
def seed_db(db):
    # Create tables
    db.execute(text("""
        CREATE TABLE mcp_policy_rules (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT,
            is_active BOOLEAN NOT NULL
        )
    """))
    db.execute(text("""
        CREATE TABLE mcp_decisions (
            id INTEGER PRIMARY KEY,
            rule_id INTEGER NOT NULL,
            decision TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """))
    db.execute(text("""
        CREATE TABLE mcp_exemptions (
            id INTEGER PRIMARY KEY,
            rule_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    """))

    # Insert test data
    db.execute(text("""
        INSERT INTO mcp_policy_rules (id, name, description, is_active)
        VALUES
            (1, 'Rule 1', 'Description for Rule 1', TRUE),
            (2, 'Rule 2', 'Description for Rule 2', FALSE),
            (3, 'Rule 3', 'Description for Rule 3', TRUE)
    """))
    db.execute(text("""
        INSERT INTO mcp_decisions (id, rule_id, decision, timestamp)
        VALUES
            (1, 1, 'Approved', '2023-01-01 10:00:00'),
            (2, 1, 'Rejected', '2023-01-02 11:00:00'),
            (3, 3, 'Approved', '2023-01-03 12:00:00')
    """))
    db.execute(text("""
        INSERT INTO mcp_exemptions (id, rule_id, reason, timestamp)
        VALUES
            (1, 1, 'Exemption reason 1', '2023-01-01 10:00:00'),
            (2, 1, 'Exemption reason 2', '2023-01-02 11:00:00'),
            (3, 3, 'Exemption reason 3', '2023-01-03 12:00:00')
    """))
    db.commit()

if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    # Seed the in-memory database
    db = SessionLocal()
    seed_db(db)
    db.close()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/compliance/overview")
    assert response.status_code == 200
    data = response.json()
    assert "active_policy_rules" in data
    assert "recent_decisions" in data
    assert "current_exemptions" in data
    assert len(data["active_policy_rules"]) > 0
    assert len(data["recent_decisions"]) > 0
    assert len(data["current_exemptions"]) > 0
    print("PASS")