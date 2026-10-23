from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
from datetime import datetime
import os
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter(prefix="/api")

class Directive(BaseModel):
    task: str
    file: str
    age_seconds: int

class StarvationResponse(BaseModel):
    starvation: dict
    directives: List[Directive]

def get_directive_files(directory: str) -> List[tuple]:
    files = []
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        if os.path.isfile(filepath):
            mtime = os.path.getmtime(filepath)
            files.append((filename, mtime))
    return files

def compute_quartiles(ages: List[int]) -> dict:
    if not ages:
        return {
            "min_s": 0,
            "p25_s": 0,
            "p50_s": 0,
            "p75_s": 0,
            "max_s": 0
        }
    sorted_ages = sorted(ages)
    n = len(sorted_ages)
    return {
        "min_s": sorted_ages[0],
        "p25_s": sorted_ages[n // 4] if n > 0 else 0,
        "p50_s": sorted_ages[n // 2] if n > 0 else 0,
        "p75_s": sorted_ages[3 * n // 4] if n > 0 else 0,
        "max_s": sorted_ages[-1]
    }

@router.get("/directives/starvation", response_model=StarvationResponse)
async def get_directive_starvation(
    threshold_s: int = 3600,
    session: McpServerRegistry = Depends(get_session)
):
    pending_dir = "directives/pending"
    proposed_dir = "directives/proposed"

    pending_files = get_directive_files(pending_dir)
    proposed_files = get_directive_files(proposed_dir)

    all_files = pending_files + proposed_files
    directives = []
    ages = []

    now = datetime.now().timestamp()
    for filename, mtime in all_files:
        age_seconds = int(now - mtime)
        task = filename.split("_")[0] if "_" in filename else filename
        directives.append({
            "task": task,
            "file": filename,
            "age_seconds": age_seconds
        })
        ages.append(age_seconds)

    quartiles = compute_quartiles(ages)
    old_count = sum(1 for age in ages if age > threshold_s)

    return {
        "starvation": {
            **quartiles,
            "old_count": old_count,
            "threshold_s": threshold_s
        },
        "directives": directives
    }

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import tempfile
    import time

    test_app = FastAPI()
    test_app.include_router(router)

    with tempfile.TemporaryDirectory() as temp_dir:
        # Create test files with different ages
        file1 = os.path.join(temp_dir, "task1_10s.txt")
        file2 = os.path.join(temp_dir, "task2_120s.txt")
        file3 = os.path.join(temp_dir, "task3_7200s.txt")

        with open(file1, "w") as f:
            f.write("test")
        time.sleep(10)

        with open(file2, "w") as f:
            f.write("test")
        time.sleep(120)

        with open(file3, "w") as f:
            f.write("test")
        time.sleep(7200)

        # Override the directive directories for testing
        def mock_get_directive_files(directory: str) -> List[tuple]:
            if directory == "directives/pending":
                return [
                    ("task1_10s.txt", os.path.getmtime(file1)),
                    ("task2_120s.txt", os.path.getmtime(file2)),
                    ("task3_7200s.txt", os.path.getmtime(file3))
                ]
            return []

        router.get_directive_files = mock_get_directive_files

        client = TestClient(test_app)
        response = client.get("/api/directives/starvation?threshold_s=3600")
        assert response.status_code == 200
        data = response.json()
        assert data["starvation"]["max_s"] >= 7000
        assert data["starvation"]["old_count"] >= 1
        print("PASS")