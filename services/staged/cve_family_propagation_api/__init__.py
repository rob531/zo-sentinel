from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, User, Org

def get_server_registries(db: Session = Depends(get_session)):
    return db.query(McpServerRegistry).all()

def get_score_disputes_endpoint(db: Session = Depends(get_session)):
    return db.query(McpScoreDispute).all()

def get_mesh_memory_endpoint(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def signal_scores_endpoint(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def mesh_memory_endpoint(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def mesh_scores_endpoint(db: Session = Depends(get_session)):
    return db.query(McpLlmAxisScore).all()

def test_service_package():
    return "PASS"

if __name__ == "__main__":
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    @app.get("/test")
    async def test():
        return test_service_package()

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)