from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import Optional
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
import json

app = FastAPI()

def get_mesh_scores_endpoint():
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores():
    session: Session = Depends(get_session)
    try:
        scores = session.query(McpLlmAxisScore).all()
        return [{"id": score.id, "score": score.score} for score in scores]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory():
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def mesh_scores_endpoint():
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mcp_signal_scores"}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def mesh_memory_endpoint():
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def _run_self_test():
    try:
        # Test get_mesh_scores_endpoint
        mesh_scores = get_mesh_scores_endpoint()
        assert isinstance(mesh_scores, list)

        # Test get_signal_scores
        session: Session = Depends(get_session)
        signal_scores = get_signal_scores()
        assert isinstance(signal_scores, list)

        # Test get_mesh_memory
        mesh_memory = get_mesh_memory()
        assert isinstance(mesh_memory, list)

        # Test mesh_scores_endpoint
        scores_endpoint = mesh_scores_endpoint()
        assert isinstance(scores_endpoint, list)

        # Test mesh_memory_endpoint
        memory_endpoint = mesh_memory_endpoint()
        assert isinstance(memory_endpoint, list)

        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")

if __name__ == "__main__":
    _run_self_test()