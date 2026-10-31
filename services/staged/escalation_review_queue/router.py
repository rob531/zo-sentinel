from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry
from .logic import get_escalation_review_queue

router = APIRouter(
    prefix="/api/escalation-review-queue",
    tags=["escalation-review-queue"],
)


@router.get("")
def list_escalation_review_queue(
    session: Session = Depends(get_session),
):
    return get_escalation_review_queue(session)


def _run_self_test() -> None:
    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: None

    original_get_queue = get_escalation_review_queue
    try:
        globals()["get_escalation_review_queue"] = lambda session: {
            "items": [],
            "total": 0,
        }
        with TestClient(test_app) as client:
            response = client.get("/api/escalation-review-queue")
            assert response.status_code == 200
            assert response.json() == {"items": [], "total": 0}
    finally:
        globals()["get_escalation_review_queue"] = original_get_queue
        test_app.dependency_overrides.clear()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()