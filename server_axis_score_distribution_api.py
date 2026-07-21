from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Optional
from sqlalchemy import text, func
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLMAxisScore

router = APIRouter()

class AxisDistributionResponse(BaseModel):
    axes: Dict[str, Dict[str, object]]

class AxisDistribution(BaseModel):
    total_rows: int
    band_edges: List[float]
    band_counts: List[int]

@router.get("/servers/axis-distribution", response_model=AxisDistributionResponse)
async def get_axis_distribution(
    axis: Optional[str] = None,
    band_count: int = 5,
    session: Session = Depends(get_session)
):
    axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface"
    ]

    if axis and axis not in axes:
        raise HTTPException(status_code=400, detail="Invalid axis name")

    result = {}
    for a in axes if not axis else [axis]:
        # Get all p_top values for the axis
        query = text(f"""
            SELECT p_top FROM mcp_llm_axis_scores
            WHERE axis_name = :axis_name
        """)
        rows = session.execute(query, {"axis_name": a}).fetchall()
        p_top_values = [row[0] for row in rows]

        if not p_top_values:
            result[a] = {
                "total_rows": 0,
                "band_edges": [0.0] * (band_count + 1),
                "band_counts": [0] * band_count
            }
            continue

        # Calculate band edges and counts
        min_val = min(p_top_values)
        max_val = max(p_top_values)
        band_width = (max_val - min_val) / band_count

        band_edges = [min_val + i * band_width for i in range(band_count + 1)]
        band_counts = [0] * band_count

        for val in p_top_values:
            for i in range(band_count):
                if band_edges[i] <= val < band_edges[i + 1]:
                    band_counts[i] += 1
                    break
            else:
                band_counts[-1] += 1  # Handle max_val case

        result[a] = {
            "total_rows": len(p_top_values),
            "band_edges": band_edges,
            "band_counts": band_counts
        }

    return {"axes": result}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.include_router(router)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Seed test data
    with SessionLocal() as session:
        test_data = [
            {"axis_name": "overall_risk", "p_top": 0.1},
            {"axis_name": "overall_risk", "p_top": 0.2},
            {"axis_name": "overall_risk", "p_top": 0.3},
            {"axis_name": "auth_strength", "p_top": 0.4},
            {"axis_name": "auth_strength", "p_top": 0.5},
            {"axis_name": "capability_breadth", "p_top": 0.6},
            {"axis_name": "data_sensitivity", "p_top": 0.7},
            {"axis_name": "network_egress", "p_top": 0.8},
            {"axis_name": "maintainer_trust", "p_top": 0.9},
            {"axis_name": "exploit_surface", "p_top": 1.0},
        ]
        for data in test_data:
            session.add(MCPLLMAxisScore(**data))
        session.commit()

    client = TestClient(app)
    response = client.get("/servers/axis-distribution")

    assert response.status_code == 200
    data = response.json()
    assert "axes" in data
    assert len(data["axes"]) == 7
    for axis in data["axes"]:
        assert "total_rows" in data["axes"][axis]
        assert "band_edges" in data["axes"][axis]
        assert "band_counts" in data["axes"][axis]
        assert isinstance(data["axes"][axis]["total_rows"], int)
        assert isinstance(data["axes"][axis]["band_edges"], list)
        assert isinstance(data["axes"][axis]["band_counts"], list)
        assert len(data["axes"][axis]["band_edges"]) == 6  # band_count + 1
        assert len(data["axes"][axis]["band_counts"]) == 5  # band_count
        assert sum(data["axes"][axis]["band_counts"]) == data["axes"][axis]["total_rows"]

    print("PASS")