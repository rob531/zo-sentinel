# app_scoring_consumer.py
from typing import Dict, Any, List, Optional

from fastapi import FastAPI, APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Float, MetaData, Table
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.sql import select

# --- Pydantic Models ---

class AxisScore(BaseModel):
    label: str
    p_top: float

class VerdictResponse(BaseModel):
    axes: Dict[str, AxisScore]
    overall: float
    risk_tier: str
    criteria_version: str

# --- Database Setup (In-memory SQLite for testing) ---

DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(DATABASE_URL)
metadata = MetaData()

# Define the table structure for mcp_llm_axis_scores
mcp_llm_axis_scores_table = Table(
    "mcp_llm_axis_scores",
    metadata,
    Column("server_id", Integer, primary_key=True),
    Column("axis_name", String, primary_key=True),
    Column("p_top", Float),
    Column("criteria_version", String),
)

# Define a table for risk tier mapping (simplified for this example)
risk_tier_mapping_table = Table(
    "risk_tier_mapping",
    metadata,
    Column("risk_score", Integer, primary_key=True),
    Column("tier_name", String),
)

metadata.create_all(engine)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# --- Database Operations ---

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_risk_axes_and_overall_score(db: Session, server_id: int) -> Dict[str, Any]:
    """
    Fetches risk axes scores and overall risk for a given server_id.
    """
    stmt = select(mcp_llm_axis_scores_table).where(mcp_llm_axis_scores_table.c.server_id == server_id)
    result = db.execute(stmt).fetchall()

    if not result:
        raise HTTPException(status_code=404, detail="Server not found")

    axes_data = {}
    overall_risk_score = 0.0
    criteria_version = ""
    for row in result:
        axis_name = row.axis_name
        p_top = row.p_top
        criteria_version = row.criteria_version
        axes_data[axis_name] = {"label": axis_name, "p_top": p_top}
        # Assuming overall risk is the average of all axis scores for simplicity
        overall_risk_score += p_top

    overall_risk_score /= len(axes_data) if axes_data else 1

    return {
        "axes": axes_data,
        "overall": overall_risk_score,
        "criteria_version": criteria_version,
    }

def get_risk_tier(overall_risk_score: float) -> str:
    """
    Determines the risk tier based on the overall risk score.
    This is a simplified mapping. In a real scenario, this would likely
    come from a configuration or another table.
    """
    if overall_risk_score >= 0.8:
        return "CRITICAL"
    elif overall_risk_score >= 0.5:
        return "HIGH"
    elif overall_risk_score >= 0.2:
        return "MEDIUM"
    else:
        return "LOW"

# --- FastAPI Router ---

router = APIRouter()

@router.get("/servers/{server_id}/verdict", response_model=VerdictResponse)
async def get_server_verdict(server_id: int, db: Session = Depends(get_db)):
    """
    Retrieves the risk verdict for a given server.
    Applies rule-override: a CRITICAL axis forces the tier to CRITICAL.
    """
    server_data = get_risk_axes_and_overall_score(db, server_id)
    axes = server_data["axes"]
    overall_risk = server_data["overall"]
    criteria_version = server_data["criteria_version"]

    # Rule-override: Check for any CRITICAL axis
    is_critical_axis_present = False
    for axis_name, score_data in axes.items():
        # Assuming a threshold for an axis to be considered "CRITICAL"
        if score_data["p_top"] >= 0.9: # Example threshold for a critical axis
            is_critical_axis_present = True
            break

    if is_critical_axis_present:
        risk_tier = "CRITICAL"
    else:
        risk_tier = get_risk_tier(overall_risk)

    # Format the response according to the Pydantic model
    formatted_axes = {
        axis_name: AxisScore(label=score_data["label"], p_top=score_data["p_top"])
        for axis_name, score_data in axes.items()
    }

    return VerdictResponse(
        axes=formatted_axes,
        overall=overall_risk,
        risk_tier=risk_tier,
        criteria_version=criteria_version,
    )

# --- FastAPI App ---

app = FastAPI()
app.include_router(router)

# --- Self-Test ---

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Seed the in-memory database
    db = next(get_db())

    # Sample data for multiple servers and axes
    sample_scores = [
        {"server_id": 1, "axis_name": "axis_a", "p_top": 0.7, "criteria_version": "v1.0"},
        {"server_id": 1, "axis_name": "axis_b", "p_top": 0.8, "criteria_version": "v1.0"},
        {"server_id": 1, "axis_name": "axis_c", "p_top": 0.6, "criteria_version": "v1.0"},
        {"server_id": 1, "axis_name": "axis_d", "p_top": 0.5, "criteria_version": "v1.0"},
        {"server_id": 1, "axis_name": "axis_e", "p_top": 0.7, "criteria_version": "v1.0"},
        {"server_id": 1, "axis_name": "axis_f", "p_top": 0.8, "criteria_version": "v1.0"},
        {"server_id": 1, "axis_name": "overall_risk", "p_top": 0.0, "criteria_version": "v1.0"}, # Placeholder for overall, will be calculated

        {"server_id": 2, "axis_name": "axis_a", "p_top": 0.95, "criteria_version": "v1.1"}, # Critical axis
        {"server_id": 2, "axis_name": "axis_b", "p_top": 0.3, "criteria_version": "v1.1"},
        {"server_id": 2, "axis_name": "axis_c", "p_top": 0.4, "criteria_version": "v1.1"},
        {"server_id": 2, "axis_name": "axis_d", "p_top": 0.2, "criteria_version": "v1.1"},
        {"server_id": 2, "axis_name": "axis_e", "p_top": 0.1, "criteria_version": "v1.1"},
        {"server_id": 2, "axis_name": "axis_f", "p_top": 0.5, "criteria_version": "v1.1"},
        {"server_id": 2, "axis_name": "overall_risk", "p_top": 0.0, "criteria_version": "v1.1"}, # Placeholder

        {"server_id": 3, "axis_name": "axis_a", "p_top": 0.1, "criteria_version": "v1.2"},
        {"server_id": 3, "axis_name": "axis_b", "p_top": 0.2, "criteria_version": "v1.2"},
        {"server_id": 3, "axis_name": "axis_c", "p_top": 0.15, "criteria_version": "v1.2"},
        {"server_id": 3, "axis_name": "axis_d", "p_top": 0.05, "criteria_version": "v1.2"},
        {"server_id": 3, "axis_name": "axis_e", "p_top": 0.1, "criteria_version": "v1.2"},
        {"server_id": 3, "axis_name": "axis_f", "p_top": 0.2, "criteria_version": "v1.2"},
        {"server_id": 3, "axis_name": "overall_risk", "p_top": 0.0, "criteria_version": "v1.2"}, # Placeholder
    ]

    for score in sample_scores:
        db.execute(
            mcp_llm_axis_scores_table.insert().values(**score)
        )
    db.commit()

    client = TestClient(app)

    # Test case 1: Server with no critical axis, should determine tier based on average
    response1 = client.get("/servers/1/verdict")
    assert response1.status_code == 200
    data1 = response1.json()
    # Expected overall risk: (0.7+0.8+0.6+0.5+0.7+0.8)/6 = 0.6833...
    # Expected tier: HIGH (since 0.6833 is between 0.5 and 0.8)
    assert data1["overall"] == 0.6833333333333333
    assert data1["risk_tier"] == "HIGH"
    assert len(data1["axes"]) == 6
    assert "axis_a" in data1["axes"]
    assert data1["axes"]["axis_a"]["label"] == "axis_a"
    assert data1["axes"]["axis_a"]["p_top"] == 0.7
    assert data1["criteria_version"] == "v1.0"

    # Test case 2: Server with a critical axis, should force CRITICAL tier
    response2 = client.get("/servers/2/verdict")
    assert response2.status_code == 200
    data2 = response2.json()
    # Expected overall risk: (0.95+0.3+0.4+0.2+0.1+0.5)/6 = 0.425
    # Expected tier: CRITICAL (due to axis_a being 0.95)
    assert data2["overall"] == 0.425
    assert data2["risk_tier"] == "CRITICAL"
    assert len(data2["axes"]) == 6
    assert "axis_a" in data2["axes"]
    assert data2["axes"]["axis_a"]["p_top"] == 0.95
    assert data2["criteria_version"] == "v1.1"

    # Test case 3: Server with low scores, should be LOW tier
    response3 = client.get("/servers/3/verdict")
    assert response3.status_code == 200
    data3 = response3.json()
    # Expected overall risk: (0.1+0.2+0.15+0.05+0.1+0.2)/6 = 0.1333...
    # Expected tier: LOW (since 0.1333 is less than 0.2)
    assert data3["overall"] == 0.13333333333333333
    assert data3["risk_tier"] == "LOW"
    assert len(data3["axes"]) == 6
    assert "axis_a" in data3["axes"]
    assert data3["axes"]["axis_a"]["p_top"] == 0.1
    assert data3["criteria_version"] == "v1.2"

    # Test case 4: Non-existent server
    response4 = client.get("/servers/999/verdict")
    assert response4.status_code == 404
    assert response4.json() == {"detail": "Server not found"}

    print("PASS")