import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Org, User
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch
import json

@pytest.fixture
def client():
    with TestClient(app) as client:
        yield client

def test_risk_tier_distribution(client):
    # Setup test data in the app database
    with patch('app.db.get_session') as mock_get_session:
        # Create a test session
        test_engine = create_engine('sqlite:///:memory:')
        TestSession = sessionmaker(bind=test_engine)
        test_session = TestSession()

        # Mock the session
        mock_get_session.return_value = test_session

        # Create test tables
        test_session.execute("""
            CREATE TABLE mcp_server_registry (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR(255) NOT NULL,
                org_id INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR(255) NOT NULL,
                axis VARCHAR(255) NOT NULL,
                score FLOAT NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            CREATE TABLE mcp_score_disputes (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR(255) NOT NULL,
                axis VARCHAR(255) NOT NULL,
                dispute_reason TEXT,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            CREATE TABLE orgs (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                email VARCHAR(255) NOT NULL,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
        """)

        # Insert test data
        test_session.execute("""
            INSERT INTO orgs (id, name, created_at, updated_at)
            VALUES (1, 'Test Org', '2023-01-01 00:00:00', '2023-01-01 00:00:00');
        """)

        test_session.execute("""
            INSERT INTO mcp_server_registry (id, server_id, org_id, created_at, updated_at)
            VALUES
                (1, 'server1', 1, '2023-01-01 00:00:00', '2023-01-01 00:00:00'),
                (2, 'server2', 1, '2023-01-01 00:00:00', '2023-01-01 00:00:00'),
                (3, 'server3', 1, '2023-01-01 00:00:00', '2023-01-01 00:00:00');
        """)

        test_session.execute("""
            INSERT INTO mcp_llm_axis_scores (id, server_id, axis, score, created_at, updated_at)
            VALUES
                (1, 'server1', 'risk', 0.1, '2023-01-01 00:00:00', '2023-01-01 00:00:00'),
                (2, 'server1', 'trust', 0.8, '2023-01-01 00:00:00', '2023-01-01 00:00:00'),
                (3, 'server2', 'risk', 0.5, '2023-01-01 00:00:00', '2023-01-01 00:00:00'),
                (4, 'server2', 'trust', 0.3, '2023-01-01 00:00:00', '2023-01-01 00:00:00'),
                (5, 'server3', 'risk', 0.9, '2023-01-01 00:00:00', '2023-01-01 00:00:00'),
                (6, 'server3', 'trust', 0.2, '2023-01-01 00:00:00', '2023-01-01 00:00:00');
        """)

        test_session.commit()

        # Make the request
        response = client.get("/risk-tier-distribution")

        # Verify the response
        assert response.status_code == 200
        data = response.json()
        assert data == {
            "low_risk": 1,
            "medium_risk": 1,
            "high_risk": 1
        }

if __name__ == "__main__":
    # Run the test
    test_risk_tier_distribution(TestClient(app))
    print("PASS")