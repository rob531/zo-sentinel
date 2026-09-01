from typing import Any, Dict, List, Optional, Tuple
from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
import json

def get_mesh_scores(server_ids: List[int]) -> Dict[int, Dict[str, float]]:
    """Fetch mesh scores for given server IDs from ZoComputer store."""
    query = {
        "query": """
        SELECT server_id, axis, score
        FROM mcp_signal_scores
        WHERE server_id IN ({})
        """.format(",".join(map(str, server_ids))),
        "params": {}
    }
    response = requests.post("http://127.0.0.1:8772/query", json=query)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    result = response.json()
    scores = {}
    for row in result:
        server_id = row["server_id"]
        axis = row["axis"]
        score = row["score"]
        if server_id not in scores:
            scores[server_id] = {}
        scores[server_id][axis] = score
    return scores

def get_signal_scores(server_ids: List[int]) -> Dict[int, Dict[str, float]]:
    """Fetch signal scores for given server IDs from ZoComputer store."""
    query = {
        "query": """
        SELECT server_id, axis, score
        FROM mcp_signal_scores
        WHERE server_id IN ({})
        """.format(",".join(map(str, server_ids))),
        "params": {}
    }
    response = requests.post("http://127.0.0.1:8772/query", json=query)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch signal scores")
    result = response.json()
    scores = {}
    for row in result:
        server_id = row["server_id"]
        axis = row["axis"]
        score = row["score"]
        if server_id not in scores:
            scores[server_id] = {}
        scores[server_id][axis] = score
    return scores

def get_mesh_memory(server_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """Fetch mesh memory for given server IDs from ZoComputer store."""
    query = {
        "query": """
        SELECT server_id, memory
        FROM mesh_memory
        WHERE server_id IN ({})
        """.format(",".join(map(str, server_ids))),
        "params": {}
    }
    response = requests.post("http://127.0.0.1:8772/query", json=query)
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    result = response.json()
    memory = {}
    for row in result:
        server_id = row["server_id"]
        memory_data = json.loads(row["memory"])
        memory[server_id] = memory_data
    return memory

def _dummy_post() -> None:
    """Dummy post function for testing."""
    pass

def _post_query() -> None:
    """Post query function for testing."""
    pass

def setup_database() -> None:
    """Setup database function for testing."""
    pass

def _run_self_test() -> None:
    """Run self-test function for testing."""
    pass

def main() -> None:
    """Main function for testing."""
    pass

if __name__ == "__main__":
    # Self-test
    try:
        # Test get_mesh_scores
        test_server_ids = [1, 2, 3]
        mesh_scores = get_mesh_scores(test_server_ids)
        if not isinstance(mesh_scores, dict):
            raise ValueError("get_mesh_scores did not return a dictionary")

        # Test get_signal_scores
        signal_scores = get_signal_scores(test_server_ids)
        if not isinstance(signal_scores, dict):
            raise ValueError("get_signal_scores did not return a dictionary")

        # Test get_mesh_memory
        mesh_memory = get_mesh_memory(test_server_ids)
        if not isinstance(mesh_memory, dict):
            raise ValueError("get_mesh_memory did not return a dictionary")

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")