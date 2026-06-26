import sys
from fastapi import APIRouter, FastAPI, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Dict, List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Float
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.sql import text
from fastapi.testclient import TestClient

# --- Pydantic Models ---

class AxisScore(BaseModel):
    """Represents the score and label for a single risk axis."""
    label: str = Field(..., description="Human-readable label for the risk level (e.g., 'Low', 'Critical')")
    p_top: float = Field(..., ge=0.0, le=1.0, description="Probability of being in the top risk category (0.0 to 1.0)")

class VerdictBreakdownResponse(BaseModel):
    """The complete response model for the verdict breakdown endpoint."""
    axes: Dict[str, AxisScore] = Field(..., description="Scores for individual risk axes (e.g., data_exfiltration, privilege_escalation)")
    overall: AxisScore = Field(..., description="Overall risk score for the server")
    risk_tier: str = Field(..., description="Calculated risk tier (e.g., 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')")
    criteria_version: str = Field("v2.0", description="Version of the risk criteria used for calculation")

# --- Risk Tier Logic ---

# Define thresholds for different risk tiers based on p_top values.
RISK_TIER_THRESHOLDS = {
    "CRITICAL": 0.8,
    "HIGH": 0.6,
    "MEDIUM": 0.3,
    "LOW": 0.0, # Any p_top below MEDIUM is considered LOW
}

def calculate_risk_tier(p_top: float) -> str:
    """
    Calculates the risk tier string based on a given p_top score.
    """
    if p_top >= RISK_TIER_THRESHOLDS["CRITICAL"]:
        return "CRITICAL"
    elif p_top >= RISK_TIER_THRESHOLDS["HIGH"]:
        return "HIGH"
    elif p_top >= RISK_TIER_THRESHOLDS["MEDIUM"]:
        return "MEDIUM"
    else:
        return "LOW"

# --- Database Setup (for SQLAlchemy ORM/Core) ---

# Base class for declarative models
Base = declarative_base()

class McpLlmAxisScore(Base):
    """SQLAlchemy model for the mcp_llm_axis_scores table."""
    __tablename__ = "mcp_llm_axis_scores"
    server_id = Column(Integer, primary_key=True)
    axis_name = Column(String, primary_key=True) # e.g., 'data_exfiltration', 'overall_risk'
    label = Column(String, nullable=False)
    p_top = Column(Float, nullable=False)

    def __repr__(self):
        return (f"<McpLlmAxisScore(server_id={self.server_id}, axis_name='{self.axis_name}', "
                f"label='{self.label}', p_top={self.p_top})>")

# --- FastAPI Router ---

router = APIRouter()

# Dependency to get DB session. This will be overridden for testing.
def get_db():
    """
    Default database session dependency.
    Raises NotImplementedError if called without being overridden,
    ensuring it's explicitly configured for production or testing.
    """
    raise NotImplementedError("Database session dependency not configured. "
                              "Please provide a concrete implementation or override for testing.")

@router.get("/servers/{server_id}/verdict-breakdown", response_model=VerdictBreakdownResponse)
async def get_verdict_breakdown(server_id: int, db: Session = Depends(get_db)):
    """
    Retrieves the verdict breakdown for a given server, including individual risk axes
    and an overall risk tier with a critical override.

    Args:
        server_id (int): The ID of the server to retrieve the verdict breakdown for.
        db (Session): The database session dependency.

    Returns:
        VerdictBreakdownResponse: The detailed risk breakdown for the server.

    Raises:
        HTTPException:
            - 404 if no risk scores are found for the server_id.
            - 500 if the 'overall_risk' score is missing.
            - 500 if not all 6 expected individual risk axes are found.
    """
    # Query all scores for the given server_id using Postgres-portable SQL.
    # The `text()` construct allows for raw SQL queries with parameter binding.
    query = text(
        "SELECT axis_name, label, p_top FROM mcp_llm_axis_scores WHERE server_id = :server_id"
    )
    result = db.execute(query, {"server_id": server_id}).fetchall()

    if not result:
        raise HTTPException(status_code=404, detail=f"No risk scores found for server_id {server_id}")

    axes_data: Dict[str, AxisScore] = {}
    overall_score: Optional[AxisScore] = None
    individual_axis_p_tops: List[float] = []

    # Process query results into the required structure
    for row in result:
        axis_name, label, p_top = row
        score = AxisScore(label=label, p_top=p_top)
        if axis_name == "overall_risk":
            overall_score = score
        else:
            axes_data[axis_name] = score
            individual_axis_p_tops.append(p_top)

    # Validate that essential data is present
    if overall_score is None:
        raise HTTPException(status_code=500, detail="Overall risk score not found for the server.")

    # We expect exactly 6 individual risk axes as per the task description.
    # If fewer are found, it indicates incomplete data.
    EXPECTED_INDIVIDUAL_AXES_COUNT = 6
    if len(axes_data) != EXPECTED_INDIVIDUAL_AXES_COUNT:
        raise HTTPException(
            status_code=500,
            detail=f"Expected {EXPECTED_INDIVIDUAL_AXES_COUNT} individual risk axes, "
                   f"but found {len(axes_data)} for server_id {server_id}."
        )

    # Calculate the initial risk tier based on the overall score
    final_risk_tier = calculate_risk_tier(overall_score.p_top)

    # Apply the CRITICAL override rule:
    # If any individual axis has a p_top score qualifying it as CRITICAL,
    # the final risk tier for the server must be CRITICAL.
    for p_top in individual_axis_p_tops:
        if p_top >= RISK_TIER_THRESHOLDS["CRITICAL"]:
            final_risk_tier = "CRITICAL"
            break # Override found, no need to check further

    return VerdictBreakdownResponse(
        axes=axes_data,
        overall=overall_score,
        risk_tier=final_risk_tier,
        criteria_version="v2.0"
    )

# --- Main Application Setup ---

app = FastAPI(
    title="Verdict Breakdown API",
    description="API for retrieving server risk verdict breakdowns.",
    version="2.0.0"
)
app.include_router(router)

# --- Acceptance Test (`__main__` block) ---

if __name__ == "__main__":
    print("Running acceptance test...")

    # 1. Setup in-memory SQLite database for testing
    # Using "sqlite:///:memory:" ensures a fresh, in-memory database for each test run.
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables defined by Base metadata in the in-memory database
    Base.metadata.create_all(bind=engine)

    # 2. Seed data for the test server
    test_server_id = 123
    seed_data = [
        McpLlmAxisScore(server_id=test_server_id, axis_name="data_exfiltration", label="Low", p_top=0.1),
        McpLlmAxisScore(server_id=test_server_id, axis_name="privilege_escalation", label="Medium", p_top=0.4),
        McpLlmAxisScore(server_id=test_server_id, axis_name="lateral_movement", label="High", p_top=0.7),
        McpLlmAxisScore(server_id=test_server_id, axis_name="persistence", label="Critical", p_top=0.9), # This p_top (0.9) should trigger CRITICAL override
        McpLlmAxisScore(server_id=test_server_id, axis_name="command_and_control", label="Low", p_top=0.2),
        McpLlmAxisScore(server_id=test_server_id, axis_name="defense_evasion", label="Medium", p_top=0.5),
        McpLlmAxisScore(server_id=test_server_id, axis_name="overall_risk", label="High", p_top=0.65), # Without override, this would result in 'HIGH'
    ]

    # Add seed data to the in-memory database
    db_session = SessionLocal()
    try:
        db_session.add_all(seed_data)
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        print(f"Error seeding data: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        db_session.close()

    # 3. Override the `get_db` dependency for testing
    # This ensures that the TestClient uses the in-memory database session.
    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    # 4. Create TestClient
    client = TestClient(app)

    # 5. Make request and assert the response for the main test case
    print(f"Testing /servers/{test_server_id}/verdict-breakdown with CRITICAL override...")
    response = client.get(f"/servers/{test_server_id}/verdict-breakdown")

    assert response.status_code == 200, \
        f"Test Failed: Expected status code 200, got {response.status_code}. Detail: {response.json().get('detail')}"
    data = response.json()

    # Assert all 7 axes are present (6 individual + 1 overall)
    expected_individual_axes = {
        "data_exfiltration", "privilege_escalation", "lateral_movement",
        "persistence", "command_and_control", "defense_evasion"
    }
    assert set(data["axes"].keys()) == expected_individual_