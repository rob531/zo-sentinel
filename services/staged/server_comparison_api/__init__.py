from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import requests

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_mesh_memory() -> List[Dict[str, Any]]:
    """Fetch mesh memory data from the ZoComputer store."""
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

def get_mesh_scores() -> List[Dict[str, Any]]:
    """Fetch mesh scores data from the ZoComputer store."""
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

def get_signal_scores(db: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    """Fetch signal scores from the app database."""
    try:
        scores = db.query(McpLlmAxisScore).all()
        return [{"id": score.id, "score": score.score, "axis": score.axis} for score in scores]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def reset_server_export_api_quarantine(db: Session = Depends(get_session)) -> None:
    """Reset server export API quarantine status."""
    try:
        db.query(McpServerRegistry).update({"quarantined": False})
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def setup_database(db: Session = Depends(get_session)) -> None:
    """Setup the database with initial data."""
    try:
        # Example setup, adjust as needed
        org = Org(name="Example Org")
        db.add(org)
        db.commit()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    # Test functions
    try:
        # Test get_mesh_memory
        mesh_memory = get_mesh_memory()
        assert isinstance(mesh_memory, list)

        # Test get_mesh_scores
        mesh_scores = get_mesh_scores()
        assert isinstance(mesh_scores, list)

        # Test get_signal_scores
        signal_scores = get_signal_scores()
        assert isinstance(signal_scores, list)

        # Test reset_server_export_api_quarantine
        reset_server_export_api_quarantine()

        # Test setup_database
        setup_database()

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")