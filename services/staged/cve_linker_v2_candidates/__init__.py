from fastapi import FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory

def get_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    return app

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Override for self-test
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    app = get_app()
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Create tables for self-test
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Run self-test
    uvicorn.run(app, host="127.0.0.1", port=8000)
    print("PASS")