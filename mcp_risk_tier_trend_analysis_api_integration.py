import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import get_session
from app.models import Org, MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List, Dict, Any
from unittest.mock import patch

client = TestClient(app)

def test_risk_tier_trend_analysis_api():
    # Override the database session for testing with an in-memory SQLite database
    with patch('app.db.get_session') as mock_get_session:
        # Create a mock session
        mock_session = Session()

        # Create test data
        test_org = Org(
            id=1,
            name="Test Org",
            created_at=datetime.now()
        )
        mock_session.add(test_org)

        # Create test MCPServerRegistry entries
        test_server1 = MCPServerRegistry(
            id=1,
            org_id=1,
            server_name="Server 1",
            created_at=datetime.now()
        )
        test_server2 = MCPServerRegistry(
            id=2,
            org_id=1,
            server_name="Server 2",
            created_at=datetime.now()
        )
        mock_session.add_all([test_server1, test_server2])

        # Create test MCPLLMAxisScores entries
        test_scores = [
            MCPLLMAxisScores(
                id=1,
                server_id=1,
                axis="risk",
                score=0.8,
                created_at=datetime.now() - timedelta(days=10)
            ),
            MCPLLMAxisScores(
                id=2,
                server_id=1,
                axis="risk",
                score=0.7,
                created_at=datetime.now() - timedelta(days=5)
            ),
            MCPLLMAxisScores(
                id=3,
                server_id=2,
                axis="risk",
                score=0.9,
                created_at=datetime.now() - timedelta(days=10)
            ),
            MCPLLMAxisScores(
                id=4,
                server_id=2,
                axis="risk",
                score=0.85,
                created_at=datetime.now() - timedelta(days=5)
            )
        ]
        mock_session.add_all(test_scores)

        # Commit the test data
        mock_session.commit()

        # Configure the mock to return our test session
        mock_get_session.return_value = mock_session

        # Make the API request
        response = client.get("/risk-tier-trend")

        # Assert the response
        assert response.status_code == 200
        data = response.json()

        # Expected tier distribution with trend data
        expected_data = {
            "tier_distribution": {
                "Tier 1": {"count": 0, "trend": 0.0},
                "Tier 2": {"count": 2, "trend": -0.05},
                "Tier 3": {"count": 2, "trend": 0.05},
                "Tier 4": {"count": 0, "trend": 0.0}
            },
            "overall_trend": 0.0
        }

        assert data == expected_data
        print("PASS")

if __name__ == "__main__":
    test_risk_tier_trend_analysis_api()