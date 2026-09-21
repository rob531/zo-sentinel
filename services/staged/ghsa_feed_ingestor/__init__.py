from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import User
from typing import Optional
import requests

router = APIRouter()

@router.get("/mesh_scores")
async def mesh_scores_endpoint(db: Session = Depends(get_session)):
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

@router.get("/mesh_memory")
async def mesh_memory_endpoint(db: Session = Depends(get_session)):
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

@router.get("/signal_scores")
async def signal_scores_endpoint(db: Session = Depends(get_session)):
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

def _run_self_test():
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    app.dependency_overrides[get_session] = lambda: TestSession()

    try:
        # Test mesh_scores_endpoint
        response = mesh_scores_endpoint()
        assert isinstance(response, list)

        # Test mesh_memory_endpoint
        response = mesh_memory_endpoint()
        assert isinstance(response, list)

        # Test signal_scores_endpoint
        response = signal_scores_endpoint()
        assert isinstance(response, list)

        print("PASS")
    finally:
        app.dependency_overrides.clear()

if __name__ == "__main__":
    _run_self_test()