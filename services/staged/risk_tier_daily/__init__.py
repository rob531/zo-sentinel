from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
import requests
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

def get_mesh_scores(server_id: int, db: Session = Depends(get_session)) -> Optional[List[dict]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

def get_mesh_memory(server_id: int, db: Session = Depends(get_session)) -> Optional[List[dict]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mesh_memory WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

def get_signal_scores(server_id: int, db: Session = Depends(get_session)) -> Optional[List[dict]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test get_mesh_scores
    try:
        scores = get_mesh_scores(1)
        if scores is None:
            raise Exception("get_mesh_scores returned None")
    except Exception as e:
        print(f"FAIL: get_mesh_scores test failed - {str(e)}")
        exit(1)

    # Test get_mesh_memory
    try:
        memory = get_mesh_memory(1)
        if memory is None:
            raise Exception("get_mesh_memory returned None")
    except Exception as e:
        print(f"FAIL: get_mesh_memory test failed - {str(e)}")
        exit(1)

    # Test get_signal_scores
    try:
        signal_scores = get_signal_scores(1)
        if signal_scores is None:
            raise Exception("get_signal_scores returned None")
    except Exception as e:
        print(f"FAIL: get_signal_scores test failed - {str(e)}")
        exit(1)

    print("PASS")