from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Dict, Optional
import json

app = FastAPI()

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Fetch mesh scores for a given server from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching mesh scores: {str(e)}")

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Fetch mesh memory for a given server from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching mesh memory: {str(e)}")

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Dict:
    """Fetch signal scores for a given server from the ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error fetching signal scores: {str(e)}")

def _run_self_test():
    """Self-test for the service."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test get_mesh_scores
    try:
        scores = get_mesh_scores(1)
        print("get_mesh_scores test:", scores)
    except Exception as e:
        print("get_mesh_scores test failed:", e)

    # Test get_mesh_memory
    try:
        memory = get_mesh_memory(1)
        print("get_mesh_memory test:", memory)
    except Exception as e:
        print("get_mesh_memory test failed:", e)

    # Test get_signal_scores
    try:
        signal_scores = get_signal_scores(1)
        print("get_signal_scores test:", signal_scores)
    except Exception as e:
        print("get_signal_scores test failed:", e)

    print("PASS")

if __name__ == "__main__":
    _run_self_test()