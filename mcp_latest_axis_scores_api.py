import datetime
from typing import Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.testclient import TestClient
import pytest

# Assume a database connection and schema are available
# For this example, we'll use a mock database
class MockDB:
    def __init__(self):
        self.mcp_llm_axis_scores = []

    def add_score(self, server_id: str, axis_name: str, label: str, p_top: float, p_critical: float, p_danger: float, scored_at: datetime.datetime, label_index: int):
        self.mcp_llm_axis_scores.append({
            "server_id": server_id,
            "axis_name": axis_name,
            "label": label,
            "p_top": p_top,
            "p_critical": p_critical,
            "p_danger": p_danger,
            "scored_at": scored_at,
            "label_index": label_index
        })

    def query(self, server_id: str):
        server_scores = [
            score for score in self.mcp_llm_axis_scores if score["server_id"] == server_id
        ]
        if not server_scores:
            return []

        # Group by axis_name and find the latest for each
        latest_scores_by_axis = {}
        for score in server_scores:
            axis_name = score["axis_name"]
            if axis_name not in latest_scores_by_axis:
                latest_scores_by_axis[axis_name] = score
            else:
                # Compare scored_at and then label_index for tie-breaking
                current_latest = latest_scores_by_axis[axis_name]
                if score["scored_at"] > current_latest["scored_at"]:
                    latest_scores_by_axis[axis_name] = score
                elif score["scored_at"] == current_latest["scored_at"] and score["label_index"] > current_latest["label_index"]:
                    latest_scores_by_axis[axis_name] = score
        return list(latest_scores_by_axis.values())

# Initialize mock database
mock_db = MockDB()

# Pydantic models
class AxisScore(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: datetime.datetime

# FastAPI app and router
app = FastAPI()

@app.get("/servers/{server_id}/latest_axis_scores", response_model=Dict[str, AxisScore])
async def get_latest_axis_scores(server_id: str):
    """
    Retrieves the most recent mcp_llm_axis_scores for a given server_id.
    """
    scores_data = mock_db.query(server_id)

    if not scores_data:
        raise HTTPException(status_code=404, detail="No scores found for this server")

    latest_scores_dict = {}
    for score in scores_data:
        latest_scores_dict[score["axis_name"]] = AxisScore(
            label=score["label"],
            p_top=score["p_top"],
            p_critical=score["p_critical"],
            p_danger=score["p_danger"],
            scored_at=score["scored_at"]
        )
    return latest_scores_dict

# Self-test using __main__ block
if __name__ == "__main__":
    client = TestClient(app)

    # Seed the mock database with some data
    server_id_1 = "server-123"
    server_id_2 = "server-456"

    now = datetime.datetime.now(datetime.timezone.utc)
    yesterday = now - datetime.timedelta(days=1)
    two_days_ago = now - datetime.timedelta(days=2)

    # Data for server-123
    mock_db.add_score(server_id_1, "axis_a", "low", 0.1, 0.05, 0.01, two_days_ago, 1)
    mock_db.add_score(server_id_1, "axis_a", "medium", 0.5, 0.2, 0.1, yesterday, 2) # Latest for axis_a
    mock_db.add_score(server_id_1, "axis_b", "high", 0.9, 0.8, 0.7, now, 1) # Latest for axis_b
    mock_db.add_score(server_id_1, "axis_a", "high", 0.8, 0.7, 0.6, now, 3) # Even later for axis_a, different label_index

    # Data for server-456 (should not be queried)
    mock_db.add_score(server_id_2, "axis_x", "ok", 0.6, 0.3, 0.2, now, 1)

    # Test case 1: Retrieve latest scores for server-123
    response = client.get(f"/servers/{server_id_1}/latest_axis_scores")
    assert response.status_code == 200
    expected_scores_1 = {
        "axis_a": {
            "label": "high",
            "p_top": 0.8,
            "p_critical": 0.7,
            "p_danger": 0.6,
            "scored_at": datetime.datetime(now.year, now.month, now.day, now.hour, now.minute, now.second, now.microsecond, tzinfo=datetime.timezone.utc).isoformat()
        },
        "axis_b": {
            "label": "high",
            "p_top": 0.9,
            "p_critical": 0.8,
            "p_danger": 0.7,
            "scored_at": datetime.datetime(now.year, now.month, now.day, now.hour, now.minute, now.second, now.microsecond, tzinfo=datetime.timezone.utc).isoformat()
        }
    }
    # Convert datetime objects in response to ISO format for comparison
    response_data_1 = response.json()
    for axis, score in response_data_1.items():
        score["scored_at"] = datetime.datetime.fromisoformat(score["scored_at"]).isoformat()

    assert response_data_1 == expected_scores_1

    # Test case 2: Server with no scores
    response_no_scores = client.get(f"/servers/non-existent-server/latest_axis_scores")
    assert response_no_scores.status_code == 404
    assert response_no_scores.json() == {"detail": "No scores found for this server"}

    print("PASS")