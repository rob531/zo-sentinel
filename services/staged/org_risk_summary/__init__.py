from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Dict, Optional
import json

app = FastAPI()

def get_mesh_memory() -> Dict:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_signal_scores() -> List[Dict]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_scores() -> List[Dict]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mcp_signal_scores"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def reset_server_export_api_quarantine(db: Session = Depends(get_session)) -> None:
    try:
        db.execute("UPDATE McpServerRegistry SET quarantine = FALSE WHERE quarantine = TRUE")
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

def _dummy_post() -> None:
    pass

def setup_database(db: Session = Depends(get_session)) -> None:
    try:
        db.execute("CREATE TABLE IF NOT EXISTS test_table (id INTEGER PRIMARY KEY)")
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

def _run_self_test() -> None:
    try:
        mesh_memory = get_mesh_memory()
        signal_scores = get_signal_scores()
        mesh_scores = get_mesh_scores()
        if not mesh_memory or not signal_scores or not mesh_scores:
            raise HTTPException(status_code=500, detail="Self-test failed")
        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")

if __name__ == "__main__":
    _run_self_test()