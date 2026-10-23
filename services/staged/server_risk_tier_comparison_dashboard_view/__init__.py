from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
from typing import List, Optional
import requests

router = APIRouter()

def get_mesh_memory(session: Session = Depends(get_session)):
    mesh_memory = requests.post('http://127.0.0.1:8772/query', json={
        'query': 'SELECT * FROM mesh_memory'
    }).json()
    return mesh_memory

def get_mesh_memory_by_id(id: int, session: Session = Depends(get_session)):
    mesh_memory = requests.post('http://127.0.0.1:8772/query', json={
        'query': 'SELECT * FROM mesh_memory WHERE id = :id',
        'params': {'id': id}
    }).json()
    if not mesh_memory:
        raise HTTPException(status_code=404, detail="Mesh memory not found")
    return mesh_memory[0]

def mesh_scores_endpoint(session: Session = Depends(get_session)):
    scores = session.query(McpLlmAxisScore).all()
    return scores

def mesh_memory_endpoint(session: Session = Depends(get_session)):
    mesh_memory = get_mesh_memory(session)
    return mesh_memory

def reset_server_export_quarantine_api(session: Session = Depends(get_session)):
    session.query(McpServerRegistry).update({'export_quarantine': False})
    session.commit()
    return {"status": "success"}

def reset_quarantine_endpoint(session: Session = Depends(get_session)):
    session.query(McpScoreDispute).update({'quarantine': False})
    session.commit()
    return {"status": "success"}

def mesh_scores_endpoint(session: Session = Depends(get_session)):
    scores = session.query(McpLlmAxisScore).all()
    return scores

def _run_self_test():
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    from fastapi import FastAPI
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    @app.get("/mesh_memory")
    def test_mesh_memory_endpoint():
        return mesh_memory_endpoint()

    client = TestClient(app)
    response = client.get("/mesh_memory")
    assert response.status_code == 200
    print("PASS")

if __name__ == "__main__":
    _run_self_test()