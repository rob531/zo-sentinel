from fastapi import FastAPI, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional
import os
import time
from datetime import datetime
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry

class Directive(BaseModel):
    task: str
    file: str
    age_seconds: int

class StarvationResponse(BaseModel):
    starvation: dict
    directives: List[Directive]

class DirectiveQueueStarvationTimeline:
    def __init__(self, threshold_seconds: int = 3600):
        self.threshold_seconds = threshold_seconds

    def get_directives(self, directory: str) -> List[Directive]:
        directives = []
        now = time.time()
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                mtime = os.path.getmtime(filepath)
                age_seconds = int(now - mtime)
                task = filename.split('_')[0]  # Assuming task name is first part of filename
                directives.append(Directive(task=task, file=filename, age_seconds=age_seconds))
        return directives

    def compute_quartiles(self, ages: List[int]) -> dict:
        if not ages:
            return {'min_s': 0, 'p25_s': 0, 'p50_s': 0, 'p75_s': 0, 'max_s': 0}
        ages_sorted = sorted(ages)
        n = len(ages_sorted)
        return {
            'min_s': ages_sorted[0],
            'p25_s': ages_sorted[n // 4] if n > 0 else 0,
            'p50_s': ages_sorted[n // 2] if n > 0 else 0,
            'p75_s': ages_sorted[3 * n // 4] if n > 0 else 0,
            'max_s': ages_sorted[-1]
        }

    def get_starvation_timeline(self, pending_dir: str, proposed_dir: str) -> StarvationResponse:
        pending_directives = self.get_directives(pending_dir)
        proposed_directives = self.get_directives(proposed_dir)
        all_directives = pending_directives + proposed_directives

        ages = [d.age_seconds for d in all_directives]
        quartiles = self.compute_quartiles(ages)
        old_count = sum(1 for age in ages if age > self.threshold_seconds)

        return StarvationResponse(
            starvation={
                **quartiles,
                'old_count': old_count,
                'threshold_s': self.threshold_seconds
            },
            directives=all_directives
        )

def get_directive_queue_starvation_timeline(
    pending_dir: str = "directives/pending",
    proposed_dir: str = "directives/proposed",
    threshold_seconds: int = 3600,
    db: Session = Depends(get_session)
) -> StarvationResponse:
    timeline = DirectiveQueueStarvationTimeline(threshold_seconds)
    return timeline.get_starvation_timeline(pending_dir, proposed_dir)

app = FastAPI()

@app.get("/api/directives/starvation", response_model=StarvationResponse)
async def starvation_timeline(
    pending_dir: str = "directives/pending",
    proposed_dir: str = "directives/proposed",
    threshold_seconds: int = 3600,
    db: Session = Depends(get_session)
):
    return get_directive_queue_starvation_timeline(pending_dir, proposed_dir, threshold_seconds, db)

if __name__ == "__main__":
    import tempfile
    import shutil

    # Create a temporary directory for testing
    temp_dir = tempfile.mkdtemp()

    # Create test files with different ages
    now = time.time()
    file_paths = [
        os.path.join(temp_dir, "task1_10s.txt"),
        os.path.join(temp_dir, "task2_120s.txt"),
        os.path.join(temp_dir, "task3_7200s.txt")
    ]

    # Set file modification times
    os.mknod(file_paths[0])
    os.utime(file_paths[0], (now - 10, now - 10))

    os.mknod(file_paths[1])
    os.utime(file_paths[1], (now - 120, now - 120))

    os.mknod(file_paths[2])
    os.utime(file_paths[2], (now - 7200, now - 7200))

    # Set up test client with dependency overrides
    test_app = FastAPI()
    test_app.include_router(app.router)

    from sqlalchemy.orm import sessionmaker
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    # Test the endpoint
    response = client.get("/api/directives/starvation", params={
        "pending_dir": temp_dir,
        "proposed_dir": temp_dir,
        "threshold_seconds": 3600
    })

    assert response.status_code == 200
    data = response.json()
    assert data["starvation"]["max_s"] >= 7000
    assert data["starvation"]["old_count"] >= 1

    # Clean up
    shutil.rmtree(temp_dir)

    print("PASS")