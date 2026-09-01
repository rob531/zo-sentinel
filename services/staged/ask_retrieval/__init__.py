from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests
from typing import List, Dict, Optional
import json

app = FastAPI()

def get_mesh_scores(server_ids: List[int]) -> Dict[int, Dict[str, float]]:
    """Fetch mesh scores for given server IDs from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id IN ({','.join(map(str, server_ids))})"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh scores")
    scores = {}
    for row in response.json():
        scores[row['server_id']] = {
            'risk': row['risk_score'],
            'performance': row['performance_score'],
            'security': row['security_score']
        }
    return scores

def get_mesh_memory(server_ids: List[int]) -> Dict[int, Dict[str, str]]:
    """Fetch mesh memory for given server IDs from ZoComputer store."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": f"SELECT * FROM mesh_memory WHERE server_id IN ({','.join(map(str, server_ids))})"}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")
    memory = {}
    for row in response.json():
        memory[row['server_id']] = {
            'last_updated': row['last_updated'],
            'status': row['status']
        }
    return memory

def get_signal_scores(server_ids: List[int], db: Session = Depends(get_session)) -> Dict[int, Dict[str, float]]:
    """Fetch signal scores for given server IDs from app database."""
    scores = {}
    for server_id in server_ids:
        server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
        if server:
            scores[server_id] = {
                'risk': server.risk_score,
                'performance': server.performance_score,
                'security': server.security_score
            }
    return scores

def reset_server_export_api_quarantine(server_id: int, db: Session = Depends(get_session)) -> bool:
    """Reset export API quarantine for a given server ID."""
    server = db.query(McpServerRegistry).filter(McpServerRegistry.id == server_id).first()
    if server:
        server.export_api_quarantined = False
        db.commit()
        return True
    return False

def dummy_post_endpoint(data: Dict[str, str], db: Session = Depends(get_session)) -> Dict[str, str]:
    """Dummy POST endpoint for testing."""
    return {"status": "success", "data": data}

def _run_self_test():
    """Self-test for the service."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the database session for testing
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test data
    test_server = McpServerRegistry(
        id=1,
        risk_score=0.8,
        performance_score=0.6,
        security_score=0.9,
        export_api_quarantined=True
    )
    db = SessionLocal()
    db.add(test_server)
    db.commit()

    # Test functions
    assert get_signal_scores([1]) == {1: {'risk': 0.8, 'performance': 0.6, 'security': 0.9}}
    assert reset_server_export_api_quarantine(1)
    assert dummy_post_endpoint({"key": "value"}) == {"status": "success", "data": {"key": "value"}}

    print("PASS")

if __name__ == "__main__":
    _run_self_test()