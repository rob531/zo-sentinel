from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List

from app.db import get_session

router = APIRouter()


class HandlerHealth(BaseModel):
    handler: str
    pending: int
    proposed: int
    oldest_age_seconds: int


class QueueHealthResponse(BaseModel):
    handlers: List[HandlerHealth]
    summary: dict


def get_directive_queue_health():
    import requests
    try:
        resp = requests.get(
            "http://127.0.0.1:8772/query",
            json={"type": "directive_queue_metadata"},
            timeout=5
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {"handlers": [], "summary": {"total_pending": 0, "total_proposed": 0, "oldest_overall_seconds": 0}}


@router.get("/api/directives/queue-health", response_model=QueueHealthResponse)
async def get_queue_health(
    session=Depends(get_session)
):
    data = get_directive_queue_health()
    handlers = [HandlerHealth(**h) for h in data.get("handlers", [])]
    summary = data.get("summary", {})
    if handlers and "total_pending" not in summary:
        summary = {
            "total_pending": sum(h.pending for h in handlers),
            "total_proposed": sum(h.proposed for h in handlers),
            "oldest_overall_seconds": max(h.oldest_age_seconds for h in handlers)
        }
    return QueueHealthResponse(handlers=handlers, summary=summary)


if __name__ == "__main__":
    import sys
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    def get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI
    from app.db import get_session as real_get_session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[real_get_session] = get_test_session

    client = TestClient(test_app)

    seeded_data = {
        "handlers": [
            {"handler": "generate_file", "pending": 3, "proposed": 2, "oldest_age_seconds": 300},
            {"handler": "run_script", "pending": 1, "proposed": 0, "oldest_age_seconds": 60}
        ],
        "summary": {
            "total_pending": 4,
            "total_proposed": 2,
            "oldest_overall_seconds": 300
        }
    }

    with patch("services.staged.directive_queue_health.logic.requests.get") as mock_get:
        mock_resp = type("MockResponse", (), {"json": lambda self: seeded_data, "raise_for_status": lambda self: None})()
        mock_get.return_value = mock_resp

        response = client.get("/api/directives/queue-health")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert len(data["handlers"]) == 2, f"Expected 2 handlers, got {len(data['handlers'])}"

    generate_file_handler = next(h for h in data["handlers"] if h["handler"] == "generate_file")
    assert generate_file_handler["oldest_age_seconds"] == 300, f"Expected 300, got {generate_file_handler['oldest_age_seconds']}"

    print("PASS")