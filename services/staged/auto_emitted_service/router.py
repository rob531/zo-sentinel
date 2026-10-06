# services/staged/auto_emitted_service/router.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()


@router.get("/health")
def health(session: Session = Depends(get_session)):
    """Health check endpoint."""
    return {"status": "ok"}


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In-memory test database for self-test only
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router)

    # Apply the override
    test_app.dependency_overrides[get_session] = override_get_session

    # Verify the module imports and router is registered
    import sys
    from fastapi.testclient import TestClient

    client = TestClient(test_app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    print("PASS")