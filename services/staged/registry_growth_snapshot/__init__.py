from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Dict, Optional
import requests
import json

app = FastAPI()

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> Dict:
    """Fetch mesh scores for a given server_id from the ZoComputer store."""
    query = f"""
    SELECT * FROM mcp_signal_scores
    WHERE server_id = {server_id}
    """
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh scores")
    return response.json()

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> Dict:
    """Fetch signal scores for a given server_id from the ZoComputer store."""
    query = f"""
    SELECT * FROM mcp_signal_scores
    WHERE server_id = {server_id}
    """
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching signal scores")
    return response.json()

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> Dict:
    """Fetch mesh memory for a given server_id from the ZoComputer store."""
    query = f"""
    SELECT * FROM mesh_memory
    WHERE server_id = {server_id}
    """
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error fetching mesh memory")
    return response.json()

def _run_self_test():
    """Self-test for the module."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the database session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test get_mesh_scores
    try:
        get_mesh_scores(1)
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        app.dependency_overrides.clear()

if __name__ == "__main__":
    _run_self_test()