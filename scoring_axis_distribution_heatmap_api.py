from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Dict, List
from app.db import get_session
from app.models import McpLlmAxisScores
from pydantic import BaseModel
from datetime import datetime

router = APIRouter()

class BucketData(BaseModel):
    server_id: str
    p_top: float
    p_critical: float
    scored_at: datetime

class AxisData(BaseModel):
    buckets: List[BucketData]
    percentile_p25: float
    percentile_p50: float
    percentile_p75: float
    server_count: int

@router.get("/scoring/axis-heatmap", response_model=Dict[str, AxisData])
def get_axis_heatmap(db: Session = Depends(get_session)):
    # Query all axis scores from the database
    axis_scores = db.query(McpLlmAxisScores).all()

    # Group scores by axis_name
    axis_groups = {}
    for score in axis_scores:
        if score.axis_name not in axis_groups:
            axis_groups[score.axis_name] = []
        axis_groups[score.axis_name].append({
            "server_id": score.server_id,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "scored_at": score.scored_at
        })

    # Prepare the response data
    heatmap_data = {}
    for axis_name, buckets in axis_groups.items():
        # Sort buckets by p_top for percentile calculation
        sorted_buckets = sorted(buckets, key=lambda x: x["p_top"])
        bucket_count = len(sorted_buckets)

        # Calculate percentiles
        def get_percentile(percentile: float) -> float:
            if bucket_count == 0:
                return 0.0
            index = int(percentile * (bucket_count - 1))
            return sorted_buckets[index]["p_top"]

        percentile_p25 = get_percentile(0.25)
        percentile_p50 = get_percentile(0.50)
        percentile_p75 = get_percentile(0.75)

        heatmap_data[axis_name] = {
            "buckets": buckets,
            "percentile_p25": percentile_p25,
            "percentile_p50": percentile_p50,
            "percentile_p75": percentile_p75,
            "server_count": bucket_count
        }

    return heatmap_data

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import random
    from datetime import datetime, timedelta

    # Create a test app and override the session dependency
    app = FastAPI()
    app.include_router(router)

    # Create an in-memory SQLite database for testing
    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Seed the database with test data
    test_session = TestSession()
    axes = ["axis1", "axis2", "axis3", "axis4", "axis5", "axis6", "axis7"]
    servers = ["server1", "server2", "server3"]

    for server in servers:
        for axis in axes:
            p_top = random.uniform(0, 1)
            p_critical = random.uniform(0, 1)
            scored_at = datetime.now() - timedelta(days=random.randint(0, 30))
            test_session.add(McpLlmAxisScores(
                server_id=server,
                axis_name=axis,
                p_top=p_top,
                p_critical=p_critical,
                scored_at=scored_at
            ))
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/scoring/axis-heatmap")
    assert response.status_code == 200
    data = response.json()

    # Verify the response structure and percentiles
    for axis_name, axis_data in data.items():
        assert axis_data["percentile_p25"] is not None
        assert axis_data["percentile_p50"] is not None
        assert axis_data["percentile_p75"] is not None

    print("PASS")