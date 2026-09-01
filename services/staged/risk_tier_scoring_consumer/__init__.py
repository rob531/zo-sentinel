from typing import List, Dict, Any, Optional
import requests
from fastapi import Depends, HTTPException
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from pydantic import BaseModel

class SignalScore(BaseModel):
    server_id: int
    signal_name: str
    score: float
    timestamp: str

class MeshScore(BaseModel):
    server_id: int
    mesh_score: float
    timestamp: str

class MeshMemory(BaseModel):
    server_id: int
    memory: Dict[str, Any]
    timestamp: str

def get_signal_scores(server_id: int, db_session=Depends(get_session)) -> List[SignalScore]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        results = response.json()
        return [SignalScore(**result) for result in results]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_scores(server_id: int, db_session=Depends(get_session)) -> List[MeshScore]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        results = response.json()
        return [MeshScore(**result) for result in results]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory(server_id: int, db_session=Depends(get_session)) -> List[MeshMemory]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        results = response.json()
        return [MeshMemory(**result) for result in results]
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from app.db import get_session
    # FU-369: removed an import of `override_dependencies_for_testing` from a module that does
    # not exist in this tree, together with its call below. The call
    # installed nothing this file does not already do for itself.
    # FU-369: call removed with its phantom import (see above).

    # Test data setup
    test_server = McpServerRegistry(server_id=1, org_id=1, name="Test Server")
    test_session = get_session()
    test_session.add(test_server)
    test_session.commit()

    # Test get_signal_scores
    try:
        scores = get_signal_scores(1)
        print("PASS" if scores else "FAIL")
    except Exception as e:
        print(f"FAIL: {e}")

    # Test get_mesh_scores
    try:
        mesh_scores = get_mesh_scores(1)
        print("PASS" if mesh_scores else "FAIL")
    except Exception as e:
        print(f"FAIL: {e}")

    # Test get_mesh_memory
    try:
        memories = get_mesh_memory(1)
        print("PASS" if memories else "FAIL")
    except Exception as e:
        print(f"FAIL: {e}")