# mcp_policy_rules_dashboard_api.py

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Dict, Optional
import datetime

from sqlalchemy import create_engine, Column, Integer, String, DateTime, func
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.pool import StaticPool # For in-memory SQLite testing

# --- Database Configuration (Simplified for this example) ---
# In a real application, these would typically be in a separate `database.py` file.

# SQLAlchemy Base for declarative models
Base = declarative_base()

# Database URL for the main application (can be configured via environment variables)
# For demonstration, we'll use a file-based SQLite database.
SQLALCHEMY_DATABASE_URL = "sqlite:///./mcp_policy_rules.db"

# Create the SQLAlchemy engine
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False} # Required for SQLite
)

# Create a SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Dependency to get a database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- SQLAlchemy Model ---
class MCPPolicyRule(Base):
    __tablename__ = "mcp_policy_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    description = Column(String)
    status = Column(String, default="active", nullable=False) # e.g., "active", "inactive", "draft"
    rule_type = Column(String, nullable=False) # e.g., "security", "compliance", "cost"
    severity = Column(String) # e.g., "high", "medium", "low"
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<MCPPolicyRule(id={self.id}, name='{self.name}', status='{self.status}', type='{self.rule_type}')>"

# --- Pydantic Schemas for API Response ---
class RuleStatusCount(BaseModel):
    status: str
    count: int

class MCPPolicyRulesDashboardResponse(BaseModel):
    total_rules: int
    rules_by_status: List[RuleStatusCount]
    unique_rule_types: List[str]
    last_updated: Optional[datetime.datetime] # Optional as it might be None for an empty table

# --- FastAPI Router ---
router = APIRouter(
    prefix="/dashboard",
    tags=["MCP Policy Rules Dashboard"],
    responses={404: {"description": "Not found"}},
)

@router.get("/mcp_policy_rules", response_model=MCPPolicyRulesDashboardResponse)
async def get_mcp_policy_rules_dashboard_data(db: Session = Depends(get_db)):
    """
    Retrieves aggregated data for the MCP Policy Rules Dashboard.
    Returns total rules, rules by status, unique rule types, and last updated timestamp.
    Handles cases where the table is empty.
    """
    # Total number of rules
    total_rules = db.query(func.count(MCPPolicyRule.id)).scalar()

    # Rules count by status
    rules_by_status_raw = db.query(MCPPolicyRule.status, func.count(MCPPolicyRule.id))\
                              .group_by(MCPPolicyRule.status)\
                              .all()
    rules_by_status = [RuleStatusCount(status=s, count=c) for s, c in rules_by_status_raw]

    # Unique rule types
    unique_rule_types = db.query(MCPPolicyRule.rule_type).distinct().scalars().all()

    # Last updated timestamp
    last_updated = db.query(func.max(MCPPolicyRule.updated_at)).scalar()

    return MCPPolicyRulesDashboardResponse(
        total_rules=total_rules if total_rules is not None else 0,
        rules_by_status=rules_by_status,
        unique_rule_types=unique_rule_types,
        last_updated=last_updated
    )

# --- Self-Test Implementation ---
# This section demonstrates how to test the API with an in-memory SQLite database.
# In a real project, tests would typically reside in a separate `test_*.py` file.

# Create a FastAPI app instance for testing purposes
test_app = FastAPI()
test_app.include_router(router)

# Setup for testing with an in-memory SQLite database
SQLALCHEMY_DATABASE_URL_TEST = "sqlite:///:memory:"
engine_test = create_engine(
    SQLALCHEMY_DATABASE_URL_TEST,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool, # Important for in-memory SQLite with multiple threads
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine_test)

# Override the get_db dependency for tests
def override_get_db_for_test():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()

test_app.dependency_overrides[get_db] = override_get_db_for_test

# Create a TestClient
client = TestClient(test_app)

def run_self_tests():
    print("\n--- Running Self-Tests for MCP Policy Rules Dashboard API ---")

    # --- Test Case 1: Empty Database ---
    print("\nTest Case 1: Empty Database")
    Base.metadata.create_all(bind=engine_test) # Create tables for this test
    try:
        response = client.get("/dashboard/mcp_policy_rules")
        assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
        data = response.json()

        print(f"Response (empty DB): {data}")

        assert data["total_rules"] == 0, f"Expected total_rules 0, got {data['total_rules']}"
        assert data["rules_by_status"] == [], f"Expected empty rules_by_status, got {data['rules_by_status']}"
        assert data["unique_rule_types"] == [], f"Expected empty unique_rule_types, got {data['unique_rule_types']}"
        assert data["last_updated"] is None, f"Expected last_updated None, got {data['last_updated']}"
        print("  ✅ Empty database test passed.")
    finally:
        Base.metadata.drop_all(bind=engine_test) # Clean up after test

    # --- Test Case 2: Seeded Data ---
    print("\nTest Case 2: Seeded Data")
    Base.metadata.create_all(bind=engine_test) # Create tables for this test
    try:
        db = TestingSessionLocal()
        # Seed data
        rule1 = MCPPolicyRule(name="Rule A", status="active", rule_type="security", severity="high")
        rule2 = MCPPolicyRule(name="Rule B", status="inactive", rule_type="compliance", severity="medium")
        rule3 = MCPPolicyRule(name="Rule C", status="active", rule_type="security", severity="low")
        rule4 = MCPPolicyRule(name="Rule D", status="active", rule_type="cost", severity="low")
        rule5 = MCPPolicyRule(name="Rule E", status="draft", rule_type="security", severity="medium")
        db.add_all([rule1, rule2, rule3, rule4, rule5])
        db.commit()
        db.refresh(rule1) # Refresh to get updated_at if func.now() is used
        db.refresh(rule2)
        db.refresh(rule3)
        db.refresh(rule4)
        db.refresh(rule5)

        # Get the latest updated_at from the seeded data
        expected_last_updated = max(
            rule1.updated_at, rule2.updated_at, rule3.updated_at, rule4.updated_at, rule5.updated_at
        )
        db.close()

        response = client.get("/dashboard/mcp_policy_rules")
        assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
        data = response.json()

        print(f"Response (seeded DB): {data}")

        assert data["total_rules"] == 5, f"Expected total_rules 5, got {data['total_rules']}"

        expected_rules_by_status = [
            {"status": "active", "count": 3},
            {"status": "draft", "count": 1},
            {"status": "inactive", "count": 1},
        ]
        # Sort both lists for comparison as order might not be guaranteed
        actual_rules_by_status_sorted = sorted(data["rules_by_status"], key=lambda x: x['status'])
        expected_rules_by_status_sorted = sorted(expected_rules_by_status, key=lambda x: x['status'])
        assert actual_rules_by_status_sorted == expected_rules_by_status_sorted, \
            f"Expected rules_by_status {expected_rules_by_status_sorted}, got {actual_rules_by_status_sorted}"

        expected_unique_rule_types = sorted(["security", "compliance", "cost"])
        actual_unique_rule_types_sorted = sorted(data["unique_rule_types"])
        assert actual_unique_rule_types_sorted == expected_unique_rule_types, \
            f"Expected unique_rule_types {expected_unique_rule_types}, got {actual_unique_rule_types_sorted}"

        # Compare timestamps, allowing for slight differences due to serialization/deserialization
        actual_last_updated_dt = datetime.datetime.fromisoformat(data["last_updated"])
        # Allow for a small delta, e.g., 1 second, as database timestamps might vary slightly
        time_delta = datetime.timedelta(seconds=1)
        assert expected_last_updated - time_delta <= actual_last_updated_dt <= expected_last_updated + time_delta, \
            f"Expected last_updated around {expected_last_updated}, got {actual_last_updated_dt}"

        print("  ✅ Seeded data test passed.")
    finally:
        Base.metadata.drop_all(bind=engine_test) # Clean up after test

    print("\n--- All Self-Tests Completed ---")

# --- Main Application Entry Point ---
# This block is for running the API directly and creating the initial database tables.
# To run the API: `uvicorn mcp_policy_rules_dashboard_api:app --reload`
# To run tests: `python mcp_policy_rules_dashboard_api.py` (or use pytest if tests were in a separate file)

# Create the main FastAPI app instance
app = FastAPI(
    title="MCP Policy Rules Dashboard API",
    description="API to serve aggregated data for the MCP Policy Rules dashboard.",
    version="1.0.0",
)

# Include the dashboard router
app.include_router(router)

@app.on_event("startup")
def on_startup():
    # Create database tables if they don't exist
    Base.metadata.create_all(bind=engine)
    print("Database tables created (if not already existing).")

@app.get("/", include_in_schema=False)
async def root():
    return {"message": "Welcome to the MCP Policy Rules Dashboard API. Visit /docs for API documentation."}

if __name__ == "__main__":
    # This block allows running the self-tests directly when the script is executed.
    # In a production setup, you would typically run tests using a test runner like pytest.
    run_self_tests()

    # To run the FastAPI application, use uvicorn:
    # uvicorn mcp_policy_rules_dashboard_api:app --reload --port 8000
    print("\nTo run the FastAPI application, use Uvicorn:")
    print("  uvicorn mcp_policy_rules_dashboard_api:app --reload --port 8000")
    print("Then access the API at http://127.0.0.1:8000/docs")