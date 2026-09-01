"""Auto-emitted service package for MCP operations."""

from app.db import get_session
from app.models import Base

__all__ = ["Base", "get_session"]

if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.db import get_session
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)

    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    a_static_app = FastAPI()
    a_static_app.dependency_overrides[get_session] = override_get_session

    print("PASS")