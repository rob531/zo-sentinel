"""zo-sentinel service package."""

__version__ = "1.0.0"

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
    Org,
    User,
    Perspective,
    PerspectiveSnapshot,
)

__all__ = [
    "__version__",
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
    "Perspective",
    "PerspectiveSnapshot",
]

if __name__ == "__main__":
    from fastapi import FastAPI, Depends
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    that_app = FastAPI()

    @that_app.get("/health")
    def health():
        return {"status": "ok"}

    @that_app.get("/test")
    def test_route(session: Session = Depends(get_session)):
        return {"session": str(type(session).__name__)}

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    import uvicorn

    print("PASS")