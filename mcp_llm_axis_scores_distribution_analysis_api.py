from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPLLMAxisScores

router = APIRouter()

class AxisScoreDistribution(BaseModel):
    axis_name: str
    label: str
    count: int
    percentage: float

@router.get("/llm-axis-scores-distribution", response_model=List[AxisScoreDistribution])
def get_llm_axis_scores_distribution(db: Session = Depends(get_session)):
    subquery = (
        db.query(
            MCPLLMAxisScores.axis_name,
            MCPLLMAxisScores.label,
            func.count(MCPLLMAxisScores.id).label("count")
        )
        .group_by(MCPLLMAxisScores.axis_name, MCPLLMAxisScores.label)
        .subquery()
    )

    total = db.query(func.sum(subquery.c.count)).scalar()

    results = (
        db.query(
            subquery.c.axis_name,
            subquery.c.label,
            subquery.c.count,
            (subquery.c.count * 100.0 / total).label("percentage")
        )
        .order_by(subquery.c.count.desc())
        .all()
    )

    return [
        AxisScoreDistribution(
            axis_name=row.axis_name,
            label=row.label,
            count=row.count,
            percentage=round(row.percentage, 2)
        )
        for row in results
    ]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPLLMAxisScores
    from app.main import app
    from sqlalchemy.orm import sessionmaker

    # Override the session for testing
    TestSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    Base.metadata.create_all(engine)
    test_session = TestSession()
    test_data = [
        MCPLLMAxisScores(axis_name="axis1", label="label1"),
        MCPLLMAxisScores(axis_name="axis1", label="label2"),
        MCPLLMAxisScores(axis_name="axis1", label="label1"),
        MCPLLMAxisScores(axis_name="axis2", label="label1"),
        MCPLLMAxisScores(axis_name="axis2", label="label2"),
        MCPLLMAxisScores(axis_name="axis3", label="label1"),
        MCPLLMAxisScores(axis_name="axis3", label="label1"),
        MCPLLMAxisScores(axis_name="axis4", label="label2"),
        MCPLLMAxisScores(axis_name="axis5", label="label1"),
        MCPLLMAxisScores(axis_name="axis6", label="label2"),
        MCPLLMAxisScores(axis_name="axis7", label="label1"),
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/llm-axis-scores-distribution")
    assert response.status_code == 200
    data = response.json()

    # Verify all 7 axes are present
    axes_present = {item["axis_name"] for item in data}
    assert len(axes_present) == 7

    # Verify counts are correct
    axis_counts = {}
    for item in data:
        axis_counts[item["axis_name"]] = axis_counts.get(item["axis_name"], 0) + item["count"]

    assert axis_counts["axis1"] == 3
    assert axis_counts["axis2"] == 2
    assert axis_counts["axis3"] == 2
    assert axis_counts["axis4"] == 1
    assert axis_counts["axis5"] == 1
    assert axis_counts["axis6"] == 1
    assert axis_counts["axis7"] == 1

    print("PASS")