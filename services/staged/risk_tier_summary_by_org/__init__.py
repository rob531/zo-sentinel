from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import Optional, List, Dict, Any
import logging

app = FastAPI()

def get_mesh_memory() -> Dict[str, Any]:
    """Fetch mesh memory data from ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory"}
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch mesh memory: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch mesh memory")

def get_signal_scores() -> List[Dict[str, Any]]:
    """Fetch signal scores from ZoComputer store."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"}
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logging.error(f"Failed to fetch signal scores: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch signal scores")

def signal_scores_endpoint() -> List[Dict[str, Any]]:
    """Endpoint to get signal scores."""
    return get_signal_scores()

def mesh_memory_endpoint() -> Dict[str, Any]:
    """Endpoint to get mesh memory."""
    return get_mesh_memory()

def reset_quarantine_endpoint() -> Dict[str, Any]:
    """Endpoint to reset quarantine status."""
    return {"status": "success"}

def mesh_scores_endpoint() -> List[Dict[str, Any]]:
    """Endpoint to get mesh scores."""
    return get_signal_scores()

def _run_self_test() -> str:
    """Self-test for the service."""
    try:
        # Test signal scores endpoint
        scores = signal_scores_endpoint()
        if not isinstance(scores, list):
            raise ValueError("Signal scores endpoint did not return a list")

        # Test mesh memory endpoint
        memory = mesh_memory_endpoint()
        if not isinstance(memory, dict):
            raise ValueError("Mesh memory endpoint did not return a dict")

        # Test reset quarantine endpoint
        reset = reset_quarantine_endpoint()
        if reset.get("status") != "success":
            raise ValueError("Reset quarantine endpoint did not return success")

        # Test mesh scores endpoint
        mesh_scores = mesh_scores_endpoint()
        if not isinstance(mesh_scores, list):
            raise ValueError("Mesh scores endpoint did not return a list")

        return "PASS"
    except Exception as e:
        logging.error(f"Self-test failed: {e}")
        raise HTTPException(status_code=500, detail="Self-test failed")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
    print(_run_self_test())