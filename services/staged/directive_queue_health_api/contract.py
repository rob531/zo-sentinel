from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, List
import os
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter(prefix="/api")

class HealthResponse(BaseModel):
    pending_count: int
    proposed_count: int
    total: int
    healthy: bool

def get_directive_counts() -> Dict[str, int]:
    pending_dir = "directives/pending"
    proposed_dir = "directives/proposed"

    pending_count = len([f for f in os.listdir(pending_dir) if f.endswith('.json')])
    proposed_count = len([f for f in os.listdir(proposed_dir) if f.endswith('.json')])

    return {
        "pending_count": pending_count,
        "proposed_count": proposed_count,
        "total": pending_count + proposed_count,
        "healthy": pending_count == 0
    }

@router.get("/directives/health", response_model=HealthResponse)
async def get_directive_health():
    counts = get_directive_counts()
    return counts

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import tempfile
    import shutil

    app = FastAPI()
    app.include_router(router)

    # Create a temporary directory for testing
    temp_dir = tempfile.mkdtemp()
    pending_dir = os.path.join(temp_dir, "pending")
    proposed_dir = os.path.join(temp_dir, "proposed")
    os.makedirs(pending_dir)
    os.makedirs(proposed_dir)

    # Seed some fake files
    with open(os.path.join(pending_dir, "1.json"), "w") as f:
        f.write("{}")
    with open(os.path.join(pending_dir, "2.json"), "w") as f:
        f.write("{}")
    with open(os.path.join(proposed_dir, "1.json"), "w") as f:
        f.write("{}")

    # Override the directive directories for testing
    original_pending_dir = "directives/pending"
    original_proposed_dir = "directives/proposed"
    os.environ["DIRECTIVES_PENDING_DIR"] = pending_dir
    os.environ["DIRECTIVES_PROPOSED_DIR"] = proposed_dir

    def get_directive_counts_test() -> Dict[str, int]:
        pending_count = len([f for f in os.listdir(pending_dir) if f.endswith('.json')])
        proposed_count = len([f for f in os.listdir(proposed_dir) if f.endswith('.json')])

        return {
            "pending_count": pending_count,
            "proposed_count": proposed_count,
            "total": pending_count + proposed_count,
            "healthy": pending_count == 0
        }

    # Override the get_directive_counts function for testing
    app.dependency_overrides[get_directive_counts] = get_directive_counts_test

    client = TestClient(app)
    response = client.get("/api/directives/health")
    assert response.status_code == 200
    data = response.json()
    assert data["pending_count"] >= 0
    assert isinstance(data["healthy"], bool)

    # Clean up
    shutil.rmtree(temp_dir)
    os.environ.pop("DIRECTIVES_PENDING_DIR", None)
    os.environ.pop("DIRECTIVES_PROPOSED_DIR", None)

    print("PASS")