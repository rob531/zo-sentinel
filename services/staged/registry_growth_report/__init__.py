from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
import requests

router = APIRouter()

def mesh_memory_endpoint():
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

def get_mesh_memory_by_id(mesh_id: int):
    try:
        response = requests.post("http://127.0.0.1:8772/query", json={"query": f"SELECT * FROM mesh_memory WHERE id = {mesh_id}"}, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    test_app = FastAPI()
    test_app.include_router(router)

    test_engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/test")
    async def test_endpoint():
        return {"status": "PASS"}

    import uvicorn
    uvicorn.run(test_app, host="127.0.0.1", port=8000)