"""directive_queue_health contract"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
import json
import os
import tempfile

router = APIRouter(prefix="/api/directives")


class QueueHealth(BaseModel):
    total: int
    by_handler: Dict[str, int]


class DirectiveQueueHealthResponse(BaseModel):
    pending: QueueHealth
    proposed: QueueHealth
    generated_at: str


def _count_by_handler(base_path: Path) -> Dict[str, int]:
    """Count files per handler type in directory tree."""
    counts = {}
    if base_path.exists():
        for entry in os.listdir(base_path):
            full_path = base_path / entry
            if full_path.is_dir():
                counts[entry] = len(os.listdir(full_path))
    return counts


def get_pending_path() -> Path:
    """Dependency: path to directives/pending/ directory."""
    return Path("directives/pending")


def get_proposed_path() -> Path:
    """Dependency: path to directives/proposed/ directory."""
    return Path("directives/proposed")


@router.get("/queue-health", response_model=DirectiveQueueHealthResponse)
def get_directive_queue_health(
    pending_path: Path = Depends(get_pending_path),
    proposed_path: Path = Depends(get_proposed_path),
) -> DirectiveQueueHealthResponse:
    """Get health metrics for pending and proposed directive queues."""
    pending_counts = _count_by_handler(pending_path)
    proposed_counts = _count_by_handler(proposed_path)

    return DirectiveQueueHealthResponse(
        pending=QueueHealth(
            total=sum(pending_counts.values()),
            by_handler=pending_counts,
        ),
        proposed=QueueHealth(
            total=sum(proposed_counts.values()),
            by_handler=proposed_counts,
        ),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # Pending: 3 files (2 build_service, 1 generate_file)
        pending_dir = base / "pending"
        pending_dir.mkdir()
        (pending_dir / "build_service").mkdir()
        (pending_dir / "build_service" / "d0.json").write_text("{}")
        (pending_dir / "build_service" / "d1.json").write_text("{}")
        (pending_dir / "generate_file").mkdir()
        (pending_dir / "generate_file" / "d0.json").write_text("{}")

        # Proposed: 2 files (run_script)
        proposed_dir = base / "proposed"
        proposed_dir.mkdir()
        (proposed_dir / "run_script").mkdir()
        (proposed_dir / "run_script" / "d0.json").write_text("{}")
        (proposed_dir / "run_script" / "d1.json").write_text("{}")

        app.dependency_overrides[get_pending_path] = lambda: base / "pending"
        app.dependency_overrides[get_proposed_path] = lambda: base / "proposed"

        client = TestClient(app)
        response = client.get("/api/directives/queue-health")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["pending"]["total"] == 3, f"Expected pending total 3, got {data['pending']['total']}"
        assert data["pending"]["by_handler"]["build_service"] == 2
        assert data["pending"]["by_handler"]["generate_file"] == 1
        assert data["proposed"]["total"] == 2, f"Expected proposed total 2, got {data['proposed']['total']}"
        assert data["proposed"]["by_handler"]["run_script"] == 2
        print("PASS")