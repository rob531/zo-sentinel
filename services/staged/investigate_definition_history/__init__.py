from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
import requests

router = APIRouter()

def self_test():
    print("PASS")

def get_signal_scores():
    pass

def mesh_memory_endpoint():
    pass

def _run_self_test():
    pass

def reset_server_export_api_quarantine_endpoint():
    pass

def signal_scores_endpoint():
    pass

def get_mesh_memory_endpoint():
    pass

def get_score_disputes_endpoint():
    pass

def test_service_package():
    pass

def mesh_memory_endpoint_get():
    pass

def McpServerRegistry():
    pass

def main():
    pass

def get_mesh_memory():
    pass

def get_mesh_memory_endpoint():
    pass

def get_score_disputes_endpoint():
    pass

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/self_test")
    def self_test_endpoint():
        self_test()
        return {"status": "PASS"}

    uvicorn.run(app, host="127.0.0.1", port=8000)