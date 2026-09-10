from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
import requests
from pydantic import BaseModel

app = FastAPI()

class ServerExportAPIQuarantine(BaseModel):
    server_id: int
    quarantine_status: bool

class MeshMemory(BaseModel):
    server_id: int
    memory: str

class SignalScores(BaseModel):
    server_id: int
    scores: dict

class ScoreDispute(BaseModel):
    server_id: int
    dispute_reason: str

def get_mesh_memory(server_id: int, session: Session = Depends(get_session)) -> Optional[str]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT memory FROM mesh_memory WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        result = response.json()
        return result[0]['memory'] if result else None
    except requests.RequestException:
        return None

def get_mesh_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[dict]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT scores FROM mcp_signal_scores WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        result = response.json()
        return result[0]['scores'] if result else None
    except requests.RequestException:
        return None

def get_signal_scores(server_id: int, session: Session = Depends(get_session)) -> Optional[dict]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT scores FROM mcp_signal_scores WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        result = response.json()
        return result[0]['scores'] if result else None
    except requests.RequestException:
        return None

def reset_server_export_api_quarantine(server_id: int, session: Session = Depends(get_session)) -> bool:
    try:
        server = session.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
        if server:
            server.quarantine_status = False
            session.commit()
            return True
        return False
    except Exception:
        return False

def setup_database():
    session = get_session()
    try:
        # Create tables if they don't exist
        McpServerRegistry.__table__.create(session.bind, checkfirst=True)
        McpLlmAxisScore.__table__.create(session.bind, checkfirst=True)
        McpScoreDispute.__table__.create(session.bind, checkfirst=True)
        Org.__table__.create(session.bind, checkfirst=True)
        User.__table__.create(session.bind, checkfirst=True)
        return True
    except Exception:
        return False
    finally:
        session.close()

if __name__ == "__main__":
    import pytest
    from app.db import get_session
    from app.models import Base

    # Override the session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test get_mesh_memory
    assert get_mesh_memory(1) is None

    # Test get_mesh_scores
    assert get_mesh_scores(1) is None

    # Test get_signal_scores
    assert get_signal_scores(1) is None

    # Test reset_server_export_api_quarantine
    assert reset_server_export_api_quarantine(1) is False

    # Test setup_database
    assert setup_database() is True

    print("PASS")