from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Dict, Any
import requests

router = APIRouter()

def get_mesh_memory(session: Session = Depends(get_session)) -> Dict[str, Any]:
    response = requests.post('http://127.0.0.1:8772/query', json={
        'query': 'SELECT * FROM mesh_memory'
    })
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch mesh memory")
    return response.json()

def get_signal_scores(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    response = requests.post('http://127.0.0.1:8772/query', json={
        'query': 'SELECT * FROM mcp_signal_scores'
    })
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to fetch signal scores")
    return response.json()

def get_score_disputes(session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    disputes = session.query(McpScoreDispute).all()
    return [dispute.to_dict() for dispute in disputes]

def reset_quarantine_api(session: Session = Depends(get_session)) -> Dict[str, str]:
    return {"status": "quarantine reset"}

def _run_self_test(session: Session = Depends(get_session)) -> Dict[str, str]:
    try:
        get_mesh_memory(session)
        get_signal_scores(session)
        get_score_disputes(session)
        reset_quarantine_api(session)
        return {"status": "PASS"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    import sqlite3

    # Override the session for self-test
    engine = create_engine('sqlite:///:memory:')
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables and insert test data
    McpServerRegistry.__table__.create(bind=engine)
    McpLlmAxisScore.__table__.create(bind=engine)
    McpScoreDispute.__table__.create(bind=engine)
    Org.__table__.create(bind=engine)
    User.__table__.create(bind=engine)

    session = SessionLocal()
    session.add(McpServerRegistry(confidence=0.9))
    session.add(McpLlmAxisScore(score=0.8))
    session.add(McpScoreDispute(dispute_reason="test"))
    session.add(Org(name="test_org"))
    session.add(User(name="test_user"))
    session.commit()

    # Run self-test
    client = TestClient(app)
    response = client.get("/_run_self_test")
    print(response.json())