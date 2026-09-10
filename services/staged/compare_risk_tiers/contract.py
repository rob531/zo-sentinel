from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, validator
from typing import List, Optional
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore
from datetime import datetime
from fastapi.limiting import RateLimitExceeded, RateLimiter
from fastapi.limiting.utils import get_client_ip
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

class ServerComparisonRequest(BaseModel):
    server_ids: List[str] = Field(..., min_items=2, max_items=10)
    include_metrics: bool = True
    include_visualizations: bool = True

    @validator('server_ids')
    def validate_server_ids(cls, v):
        if len(v) != len(set(v)):
            raise ValueError("Duplicate server IDs are not allowed")
        return v

class ServerComparisonResponse(BaseModel):
    server_ids: List[str]
    risk_tiers: List[str]
    metrics: Optional[dict]
    visualizations: Optional[dict]

@router.get("/compare/risk_tiers", response_model=ServerComparisonResponse)
@RateLimiter(limit=10, window=60)
async def compare_risk_tiers(
    request: ServerComparisonRequest,
    session: Session = Depends(get_session),
    ip: str = Depends(get_client_ip)
):
    try:
        # Validate server IDs exist in registry
        existing_servers = session.query(McpServerRegistry.server_id).filter(
            McpServerRegistry.server_id.in_(request.server_ids)
        ).all()
        existing_server_ids = [server[0] for server in existing_servers]

        if len(existing_server_ids) != len(request.server_ids):
            missing = set(request.server_ids) - set(existing_server_ids)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Servers not found: {', '.join(missing)}"
            )

        # Get risk tiers
        risk_tiers = session.query(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier
        ).filter(
            McpServerRegistry.server_id.in_(request.server_ids)
        ).all()

        response = {
            "server_ids": [server[0] for server in risk_tiers],
            "risk_tiers": [server[1] for server in risk_tiers],
            "metrics": {},
            "visualizations": {}
        }

        if request.include_metrics:
            # Get metrics from axis scores
            metrics = session.query(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.axis_name,
                McpLlmAxisScore.p_danger,
                McpLlmAxisScore.p_critical,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.scored_at
            ).filter(
                McpLlmAxisScore.server_id.in_(request.server_ids)
            ).all()

            # Organize metrics by server and axis
            metrics_dict = {}
            for metric in metrics:
                if metric.server_id not in metrics_dict:
                    metrics_dict[metric.server_id] = {}
                metrics_dict[metric.server_id][metric.axis_name] = {
                    "p_danger": metric.p_danger,
                    "p_critical": metric.p_critical,
                    "p_top": metric.p_top,
                    "scored_at": metric.scored_at
                }

            response["metrics"] = metrics_dict

        if request.include_visualizations:
            # Generate simple visualizations (in a real app, this would be more complex)
            visualizations = {}
            for server_id in request.server_ids:
                visualizations[server_id] = {
                    "risk_tier": "bar_chart",
                    "axis_scores": "line_chart"
                }
            response["visualizations"] = visualizations

        return response

    except Exception as e:
        logger.error(f"Error comparing risk tiers: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while comparing risk tiers"
        )

def test_router():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Mock data
    test_servers = [
        McpServerRegistry(
            server_id="server1",
            risk_tier="high",
            name="Test Server 1",
            url="http://test1.example.com",
            last_seen=datetime.now(),
            confidence=0.95
        ),
        McpServerRegistry(
            server_id="server2",
            risk_tier="medium",
            name="Test Server 2",
            url="http://test2.example.com",
            last_seen=datetime.now(),
            confidence=0.85
        )
    ]

    test_scores = [
        McpLlmAxisScore(
            server_id="server1",
            axis_name="security",
            p_danger=0.8,
            p_critical=0.6,
            p_top=0.9,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id="server1",
            axis_name="privacy",
            p_danger=0.5,
            p_critical=0.3,
            p_top=0.7,
            scored_at=datetime.now()
        ),
        McpLlmAxisScore(
            server_id="server2",
            axis_name="security",
            p_danger=0.6,
            p_critical=0.4,
            p_top=0.8,
            scored_at=datetime.now()
        )
    ]

    # Add test data
    with TestSession() as session:
        session.add_all(test_servers)
        session.add_all(test_scores)
        session.commit()

    # Setup test app
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: TestSession()

    client = TestClient(test_app)

    # Test cases
    try:
        # Test basic comparison
        response = client.get(
            "/compare/risk_tiers?server_ids=server1,server2&include_metrics=true&include_visualizations=true"
        )
        assert response.status_code == 200
        assert response.json()["server_ids"] == ["server1", "server2"]
        assert response.json()["risk_tiers"] == ["high", "medium"]
        assert "metrics" in response.json()
        assert "visualizations" in response.json()

        # Test missing server
        response = client.get(
            "/compare/risk_tiers?server_ids=server1,nonexistent"
        )
        assert response.status_code == 404

        # Test rate limiting
        for _ in range(11):
            response = client.get(
                "/compare/risk_tiers?server_ids=server1,server2"
            )
        assert response.status_code == 429

        print("PASS")

    except AssertionError as e:
        print(f"FAIL: {str(e)}")
        raise
    except Exception as e:
        print(f"FAIL: Unexpected error - {str(e)}")
        raise

if __name__ == "__main__":
    test_router()