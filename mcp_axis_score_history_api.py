import datetime
from typing import List, Dict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from starlette.testclient import TestClient

from mcp.postgres_portable import PostgresPortable


class ScoreHistoryItem(BaseModel):
    scored_at: datetime.datetime
    p_top: float


def get_axis_score_history(server_id: str, axis_name: str) -> List[ScoreHistoryItem]:
    """
    Retrieves historical 'p_top' scores for a specific risk axis of a given server.

    Args:
        server_id: The ID of the server.
        axis_name: The name of the risk axis (e.g., 'overall_risk', 'auth_strength').

    Returns:
        A list of ScoreHistoryItem objects, ordered by 'scored_at' ascending.
    """
    valid_axes = [
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    ]
    if axis_name not in valid_axes:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name. Must be one of: {', '.join(valid_axes)}",
        )

    db = PostgresPortable()
    query = """
        SELECT scored_at, p_top
        FROM mcp_llm_axis_scores
        WHERE server_id = %s AND axis_name = %s
        ORDER BY scored_at ASC;
    """
    results = db.execute(query, (server_id, axis_name))

    history = []
    for row in results:
        history.append(ScoreHistoryItem(scored_at=row[0], p_top=row[1]))

    return history


def create_app() -> FastAPI:
    app = FastAPI()

    @app.get(
        "/servers/{server_id}/axis_scores/{axis_name}/history",
        response_model=List[ScoreHistoryItem],
    )
    async def read_axis_score_history(server_id: str, axis_name: str):
        return get_axis_score_history(server_id, axis_name)

    return app


if __name__ == "__main__":
    # Seed the in-memory store for testing
    db = PostgresPortable()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
            server_id TEXT,
            axis_name TEXT,
            scored_at TIMESTAMP WITH TIME ZONE,
            p_top REAL
        );
        """
    )
    db.execute(
        """
        INSERT INTO mcp_llm_axis_scores (server_id, axis_name, scored_at, p_top) VALUES
        ('test_server_id', 'overall_risk', '2023-01-01T10:00:00Z', 0.85),
        ('test_server_id', 'overall_risk', '2023-01-02T11:00:00Z', 0.90),
        ('test_server_id', 'auth_strength', '2023-01-01T10:00:00Z', 0.70),
        ('another_server', 'overall_risk', '2023-01-01T10:00:00Z', 0.60);
        """
    )

    app = create_app()
    client = TestClient(app)

    # Test case
    response = client.get(
        "/servers/test_server_id/axis_scores/overall_risk/history"
    )
    assert response.status_code == 200
    history_data = response.json()
    assert isinstance(history_data, list)
    assert len(history_data) > 0
    for item in history_data:
        assert "scored_at" in item
        assert "p_top" in item
        assert isinstance(item["scored_at"], str)
        assert isinstance(item["p_top"], float)

    # Verify the order and values
    assert history_data[0]["scored_at"] == "2023-01-01T10:00:00+00:00"
    assert history_data[0]["p_top"] == 0.85
    assert history_data[1]["scored_at"] == "2023-01-02T11:00:00+00:00"
    assert history_data[1]["p_top"] == 0.90

    print("PASS")