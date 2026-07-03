from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, case
from typing import Dict, List
from pydantic import BaseModel
from app.db import get_session
from app.models import MCPLLMAxisScores, MCPServerRegistry

router = APIRouter()

class ScoreRangeCount(BaseModel):
    score_range: str
    count: int
    top_servers: List[str]

class AxisDistribution(BaseModel):
    axis: str
    score_distribution: List[ScoreRangeCount]

@router.get("/axis-scores-distribution", response_model=Dict[str, AxisDistribution])
async def get_axis_scores_distribution(db: Session = Depends(get_session)):
    # Define score ranges
    score_ranges = [
        (0, 20, "0-20"),
        (21, 40, "21-40"),
        (41, 60, "41-60"),
        (61, 80, "61-80"),
        (81, 100, "81-100")
    ]

    # Query for each axis
    axes = ["clarity", "coherence", "relevance", "depth", "originality", "complexity", "precision"]
    result = {}

    for axis in axes:
        # Get score distribution and top servers for each range
        query = (
            db.query(
                case(
                    *([(func.count(1), f"{r[2]}") for r in score_ranges]),
                    else_="Other"
                ),
                MCPServerRegistry.server_name
            )
            .join(MCPServerRegistry, MCPLLMAxisScores.server_id == MCPServerRegistry.id)
            .filter(MCPLLMAxisScores.axis == axis)
            .group_by(MCPServerRegistry.server_name)
            .order_by(MCPServerRegistry.server_name)
        ).all()

        # Process query results
        score_dist = {r[0]: {"count": 0, "top_servers": []} for r in score_ranges}
        server_scores = {}

        for score_range, server_name in query:
            if score_range in score_dist:
                score_dist[score_range]["count"] += 1
                if server_name not in server_scores:
                    server_scores[server_name] = 0
                server_scores[server_name] += 1

        # Get top 5 servers for each range
        for range_label in score_dist:
            sorted_servers = sorted(
                server_scores.items(),
                key=lambda x: x[1],
                reverse=True
            )[:5]
            score_dist[range_label]["top_servers"] = [s[0] for s in sorted_servers]

        # Convert to the expected format
        score_dist_list = [
            ScoreRangeCount(
                score_range=range_label,
                count=score_dist[range_label]["count"],
                top_servers=score_dist[range_label]["top_servers"]
            )
            for range_label in score_dist
        ]

        result[axis] = AxisDistribution(
            axis=axis,
            score_distribution=score_dist_list
        )

    return result

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    from app.db import get_session

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test app
    test_app = FastAPI()
    test_app.include_router(router)

    # Seed test data
    with TestSession() as session:
        # Add test servers
        servers = [
            MCPServerRegistry(server_name=f"Server {i}", server_url=f"http://server{i}.com")
            for i in range(1, 6)
        ]
        session.add_all(servers)
        session.commit()

        # Add test scores
        scores = [
            MCPLLMAxisScores(
                server_id=i,
                axis=axis,
                score=score
            )
            for i in range(1, 6)
            for axis in ["clarity", "coherence", "relevance", "depth", "originality", "complexity", "precision"]
            for score in range(0, 101, 20)
        ]
        session.add_all(scores)
        session.commit()

    # Test the endpoint
    client = TestClient(test_app)
    response = client.get("/axis-scores-distribution")
    assert response.status_code == 200
    data = response.json()

    # Verify all 7 axes are present
    assert len(data) == 7
    assert all(axis in data for axis in ["clarity", "coherence", "relevance", "depth", "originality", "complexity", "precision"])

    # Verify each axis has score distribution and top servers
    for axis_data in data.values():
        assert "axis" in axis_data
        assert "score_distribution" in axis_data
        for range_data in axis_data["score_distribution"]:
            assert "score_range" in range_data
            assert "count" in range_data
            assert "top_servers" in range_data
            assert len(range_data["top_servers"]) <= 5

    print("PASS")