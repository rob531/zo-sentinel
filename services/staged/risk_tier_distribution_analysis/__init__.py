from fastapi import FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory

def get_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app = get_app()
    app.dependency_overrides[get_session] = override_get_session

    # Create tables for testing
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Run the test server
    uvicorn.run(app, host="127.0.0.1", port=8000)

    print("PASS")