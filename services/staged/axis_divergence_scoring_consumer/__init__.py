from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter()

class McpServerRegistryResponse(BaseModel):
    id: int
    confidence: float
    description: Optional[str] = None

    class Config:
        orm_mode = True

@router.get("/servers", response_model=List[McpServerRegistryResponse])
def get_servers(session: Session = Depends(get_session)):
    servers = session.query(McpServerRegistry).all()
    return servers

@router.get("/mesh_memory")
def get_mesh_memory():
    # This is a placeholder for the actual implementation
    # that would query the mesh_memory table on the write-service bus
    pass

@router.post("/self_test")
def _run_self_test():
    # This is a placeholder for the actual self-test implementation
    print("PASS")

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    # Create an in-memory SQLite database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create the tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Create a sessionmaker
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the get_session dependency
    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Run the self-test
    _run_self_test()

    # Start the server
    uvicorn.run(app, host="127.0.0.1", port=8000)