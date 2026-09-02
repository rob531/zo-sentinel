from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

router = APIRouter()

def get_mesh_memory(session: Session = Depends(get_session)):
    mesh_memory = session.query(McpLlmAxisScore).all()
    return mesh_memory

def get_orgs(session: Session = Depends(get_session)):
    orgs = session.query(Org).all()
    return orgs

def self_test():
    try:
        response = requests.post('http://127.0.0.1:8772/query', json={'query': 'SELECT 1'}, timeout=5)
        response.raise_for_status()
        return "PASS"
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/mesh_memory")
def mesh_memory_endpoint_get(session: Session = Depends(get_session)):
    return get_mesh_memory(session)

@router.get("/orgs")
def get_orgs_endpoint(session: Session = Depends(get_session)):
    return get_orgs(session)

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    app.include_router(router)

    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    uvicorn.run(app, host="127.0.0.1", port=8000)