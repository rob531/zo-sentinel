from fastapi import APIRouter, Depends
from app.db import get_session
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import List
import json
import tempfile
import shutil
from pathlib import Path
from fastapi.testclient import TestClient

router = APIRouter()


class DirectiveQueueHealthResponse(BaseModel):
    total_pending: int
    total_proposed: int
    tasks: List[str]
    ts: str


def check_directive_queue_health(
    proposed_path: str = "/home/workspace/zo_sentinel/directives/proposed/",
    pending_path: str = "/home/workspace/zo_sentinel/directives/pending/"
) -> DirectiveQueueHealthResponse:
    proposed_dir = Path(proposed_path)
    pending_dir = Path(pending_path)

    proposed_files = list(proposed_dir.glob("*.json")) if proposed_dir.exists() else []
    pending_files = list(pending_dir.glob("*.json")) if pending_dir.exists() else []

    proposed_tasks = []
    for pf in proposed_files:
        try:
            data = json.loads(pf.read_text())
            if isinstance(data, dict) and "task" in data:
                proposed_tasks.append(data["task"])
        except Exception:
            pass

    pending_tasks = []
    for pf in pending_files:
        try:
            data = json.loads(pf.read_text())
            if isinstance(data, dict) and "task" in data:
                pending_tasks.append(data["task"])
        except Exception:
            pass

    all_tasks = proposed_tasks + pending_tasks

    return DirectiveQueueHealthResponse(
        total_pending=len(pending_files),
        total_proposed=len(proposed_files),
        tasks=all_tasks,
        ts=datetime.now(timezone.utc).isoformat(),
    )


@router.get("/api/directive-queue/health", response_model=DirectiveQueueHealthResponse)
def directive_queue_health() -> DirectiveQueueHealthResponse:
    return check_directive_queue_health()


if __name__ == "__main__":
    proposed_temp = tempfile.mkdtemp()
    pending_temp = tempfile.mkdtemp()

    (Path(proposed_temp) / "task1.json").write_text(json.dumps({"task": "alpha"}))
    (Path(pending_temp) / "task2.json").write_text(json.dumps({"task": "beta"}))

    try:
        from app.db import get_session
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

        def override_get_session():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()

        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router)

        app.dependency_overrides[get_session] = override_get_session

        client = TestClient(app)
        resp = client.get("/api/directive-queue/health")
        data = resp.json()

        total = data["total_proposed"] + data["total_pending"]
        assert total >= 2, f"total {total} < 2"
        assert len(data["tasks"]) > 0, "tasks empty"

        print("PASS")
    finally:
        shutil.rmtree(proposed_temp, ignore_errors=True)
        shutil.rmtree(pending_temp, ignore_errors=True)
        shutil.rmtree(proposed_temp, ignore_errors=True)
        shutil.rmtree(pending_temp, ignore_errors=True)