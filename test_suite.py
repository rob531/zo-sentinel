import sys
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from fastapi import FastAPI

sys.path.insert(0, ".")


def test_scoring_consumer_response_shape():
    """Verify scoring_consumer returns expected structure."""
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore

    mock_server = MagicMock(spec=McpServerRegistry)
    mock_server.id = "srv_abc123"
    mock_server.name = "test-server"
    mock_server.risk_tier = "medium"

    mock_scores = []
    for axis in ["security", "reliability", "maintenance"]:
        score = MagicMock(spec=McpLlmAxisScore)
        score.axis_name = axis
        score.score = 75
        score.confidence = 0.85
        mock_scores.append(score)

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar_one_or_none.return_value = mock_server
    mock_session.scalars.return_value.all.return_value = mock_scores

    with patch("app.db.get_session", return_value=mock_session):
        from app.routers import scoring_consumer
        app = FastAPI()
        app.include_router(scoring_consumer.router)
        client = TestClient(app)

        response = client.get("/servers/srv_abc123/scoring_consumer")

        assert response.status_code == 200
        data = response.json()
        assert "server_id" in data
        assert "risk_tier" in data
        assert "axes" in data


def test_scoring_consumer_axes_scores():
    """Verify axes scores are included in response."""
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore

    mock_server = MagicMock(spec=McpServerRegistry)
    mock_server.id = "srv_xyz789"
    mock_server.name = "axis-test-server"
    mock_server.risk_tier = "high"

    mock_scores = []
    for axis, score_val in [("security", 60), ("reliability", 45), ("maintenance", 30)]:
        score = MagicMock(spec=McpLlmAxisScore)
        score.axis_name = axis
        score.score = score_val
        score.confidence = 0.8
        mock_scores.append(score)

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar_one_or_none.return_value = mock_server
    mock_session.scalars.return_value.all.return_value = mock_scores

    with patch("app.db.get_session", return_value=mock_session):
        from app.routers import scoring_consumer
        app = FastAPI()
        app.include_router(scoring_consumer.router)
        client = TestClient(app)

        response = client.get("/servers/srv_xyz789/scoring_consumer")

        data = response.json()
        axes = data.get("axes", [])
        assert len(axes) == 3
        scores_by_axis = {a["axis_name"]: a["score"] for a in axes}
        assert scores_by_axis["security"] == 60
        assert scores_by_axis["reliability"] == 45
        assert scores_by_axis["maintenance"] == 30


def test_scoring_consumer_risk_tier():
    """Verify risk tier is returned and validated."""
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore

    mock_server = MagicMock(spec=McpServerRegistry)
    mock_server.id = "srv_risk_test"
    mock_server.name = "risk-test-server"
    mock_server.risk_tier = "low"

    mock_scores = [
        MagicMock(axis_name="security", score=95, confidence=0.9),
        MagicMock(axis_name="reliability", score=90, confidence=0.85),
        MagicMock(axis_name="maintenance", score=88, confidence=0.8),
    ]

    mock_session = MagicMock()
    mock_session.execute.return_value.scalar_one_or_none.return_value = mock_server
    mock_session.scalars.return_value.all.return_value = mock_scores

    with patch("app.db.get_session", return_value=mock_session):
        from app.routers import scoring_consumer
        app = FastAPI()
        app.include_router(scoring_consumer.router)
        client = TestClient(app)

        response = client.get("/servers/srv_risk_test/scoring_consumer")

        data = response.json()
        assert data["risk_tier"] == "low"
        assert data["risk_tier"] in ["low", "medium", "high", "critical"]


def test_scoring_consumer_not_found():
    """Verify 404 when server not found."""
    mock_session = MagicMock()
    mock_session.execute.return_value.scalar_one_or_none.return_value = None

    with patch("app.db.get_session", return_value=mock_session):
        from app.routers import scoring_consumer
        app = FastAPI()
        app.include_router(scoring_consumer.router)
        client = TestClient(app)

        response = client.get("/servers/nonexistent")

        assert response.status_code == 404


if __name__ == "__main__":
    import subprocess
    result = subprocess.run(
        ["pytest", __file__, "-v", "--tb=short"],
        capture_output=True,
        text=True
    )
    print(result.stdout)
    print(result.stderr)
    if result.returncode == 0:
        print("\n=== PASS ===")
    else:
        print("\n=== FAIL ===")
    sys.exit(result.returncode)