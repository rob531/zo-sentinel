from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter()

@router.get("/mesh_memory")
async def mesh_memory_endpoint(db: Session = Depends(get_session)):
    return {"status": "ok"}

@router.get("/mesh_memory/{id}")
async def get_mesh_memory_by_id(id: int, db: Session = Depends(get_session)):
    return {"id": id, "status": "ok"}

@router.get("/signal_scores")
async def signal_scores_endpoint(db: Session = Depends(get_session)):
    return {"status": "ok"}

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    app.include_router(router)

    @app.get("/")
    async def root():
        return {"message": "PASS"}

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)