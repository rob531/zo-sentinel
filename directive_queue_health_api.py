from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
import os
from datetime import datetime, timedelta
import json
from typing import Dict, List
from pydantic import BaseModel

router = APIRouter()

class QueueHealthResponse(BaseModel):
    proposed: int
    pending: int
    done_24h: int
    newest_done_age_min: int
    starved: bool

def get_directive_dirs() -> List[str]:
    """Get the list of directive directories from environment variable or default."""
    return os.getenv("ZO_DIRECTIVES_DIR", "directives/").split(",")

def count_directives_in_dir(directory: str, status: str) -> int:
    """Count the number of live JSON files in a directive directory for a given status."""
    count = 0
    for filename in os.listdir(directory):
        if filename.endswith(".json") and not filename.startswith((".bak", ".duplicate")):
            try:
                with open(os.path.join(directory, filename), "r") as f:
                    data = json.load(f)
                    if data.get("status") == status:
                        count += 1
            except (json.JSONDecodeError, IOError):
                continue
    return count

def get_newest_done_age_min(directory: str) -> int:
    """Get the age in minutes of the newest done directive in the directory."""
    newest_done_time = None
    for filename in os.listdir(directory):
        if filename.endswith(".json") and not filename.startswith((".bak", ".duplicate")):
            try:
                with open(os.path.join(directory, filename), "r") as f:
                    data = json.load(f)
                    if data.get("status") == "done":
                        done_time = datetime.fromisoformat(data.get("done_at", ""))
                        if newest_done_time is None or done_time > newest_done_time:
                            newest_done_time = done_time
            except (json.JSONDecodeError, IOError, ValueError):
                continue
    if newest_done_time:
        return int((datetime.now() - newest_done_time).total_seconds() / 60)
    return 0

@router.get("/factory/queue_health", response_model=QueueHealthResponse)
async def get_queue_health(db: Session = Depends(get_session)) -> QueueHealthResponse:
    """Get the health of the directive queue."""
    proposed = 0
    pending = 0
    done_24h = 0
    newest_done_age_min = 0

    for directory in get_directive_dirs():
        proposed += count_directives_in_dir(os.path.join(directory, "proposed"), "proposed")
        pending += count_directives_in_dir(os.path.join(directory, "pending"), "pending")
        done_24h += count_directives_in_dir(os.path.join(directory, "done"), "done")
        newest_done_age_min = max(newest_done_age_min, get_newest_done_age_min(os.path.join(directory, "done")))

    starved = (proposed + pending) == 0

    return QueueHealthResponse(
        proposed=proposed,
        pending=pending,
        done_24h=done_24h,
        newest_done_age_min=newest_done_age_min,
        starved=starved
    )

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    # Override the session for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine("sqlite:///:memory:")
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create tables for testing
    from app.models import Base
    Base.metadata.create_all(test_engine)

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/factory/queue_health")
    assert response.status_code == 200
    assert response.json()["proposed"] == 0
    assert response.json()["pending"] == 0
    assert response.json()["done_24h"] == 0
    assert response.json()["newest_done_age_min"] == 0
    assert response.json()["starved"] is True

    print("PASS")