from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


@router.get("/health")
def health(
    db: Session = Depends(get_session),
) -> dict:
    """Health check endpoint that verifies router integration."""
    return {"status": "ok"}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import importlib

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    print("PASS")