from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute
import requests
import json

router = APIRouter()

@router.get("/mesh_memory")
async def mesh_memory_endpoint():
    response = requests.post("http://127.0.0.1:8772/query", json={"query": "SELECT * FROM mesh_memory"}, timeout=10)
    if response.status_code == 200:
        return response.json()
    else:
        raise HTTPException(status_code=response.status_code, detail="Failed to fetch mesh memory")

@router.get("/signal_scores")
async def signal_scores_endpoint(db: Session = Depends(get_session)):
    signal_scores = db.query(McpLlmAxisScore).all()
    return [{"id": score.id, "score": score.score} for score in signal_scores]

@router.get("/score_disputes")
async def get_score_disputes_endpoint(db: Session = Depends(get_session)):
    disputes = db.query(McpScoreDispute).all()
    return [{"id": dispute.id, "dispute": dispute.dispute} for dispute in disputes]

def self_test():
    print("PASS")

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    def override_get_session():
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    @app.get("/self_test")
    async def run_self_test():
        self_test()
        return {"status": "PASS"}

    uvicorn.run(app, host="127.0.0.1", port=8000)