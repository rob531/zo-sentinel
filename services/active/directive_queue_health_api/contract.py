"""Contract self-test for directive_queue_health_api."""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import List

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel


class StalledDirective(BaseModel):
    task: str
    handler: str
    mtime_hours_ago: int


class HealthResponse(BaseModel):
    total_pending: int
    total_proposed: int
    by_handler: dict[str, int]
    stalled: List[StalledDirective]
    generated_at: str


def _load_directives(base_dir: Path) -> dict:
    """Load directives from pending and proposed directories."""
    from collections import defaultdict

    pending_dir = base_dir / "directives" / "pending"
    proposed_dir = base_dir / "directives" / "proposed"

    directives = {"pending": [], "proposed": []}
    by_handler = defaultdict(int)

    for d, lst in [(pending_dir, "pending"), (proposed_dir, "proposed")]:
        if d.exists():
            for f in d.glob("*.json"):
                try:
                    data = json.loads(f.read_text())
                    task = data.get("task", f.stem)
                    handler = data.get("handler", "unknown")
                    reads = data.get("reads", [])
                    desc = data.get("description", "")
                    desc_len = len(desc)

                    entry = {
                        "task": task,
                        "handler": handler,
                        "description_length": desc_len,
                        "reads": reads,
                    }
                    directives[lst].append(entry)
                    by_handler[handler] += 1
                except Exception:
                    pass

    return {"directives": directives, "by_handler": dict(by_handler)}


def _get_stalled_directives(base_dir: Path, stale_hours: int = 72) -> List[dict]:
    """Get stalled directives (no progress in >stale_hours by mtime)."""
    pending_dir = base_dir / "directives" / "pending"
    proposed_dir = base_dir / "directives" / "proposed"
    stalled = []
    now = time.time()

    for d in [pending_dir, proposed_dir]:
        if d.exists():
            for f in d.glob("*.json"):
                try:
                    mtime = f.stat().st_mtime
                    hours_ago = int((now - mtime) / 3600)
                    if hours_ago > stale_hours:
                        data = json.loads(f.read_text())
                        stalled.append({
                            "task": data.get("task", f.stem),
                            "handler": data.get("handler", "unknown"),
                            "mtime_hours_ago": hours_ago,
                        })
                except Exception:
                    pass

    return stalled


def get_directive_health(base_dir: Path) -> dict:
    """Compute directive queue health metrics."""
    from datetime import datetime, timezone

    data = _load_directives(base_dir)
    stalled = _get_stalled_directives(base_dir)

    return {
        "total_pending": len(data["directives"]["pending"]),
        "total_proposed": len(data["directives"]["proposed"]),
        "by_handler": data["by_handler"],
        "stalled": stalled,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def create_router(base_dir: Path):
    """Create router with base_dir injected."""
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/api/directives/health")
    def get_health() -> HealthResponse:
        return HealthResponse(**get_directive_health(base_dir))

    return router


def create_app(base_dir: Path) -> FastAPI:
    """Create FastAPI app for the service."""
    app = FastAPI()
    app.include_router(create_router(base_dir))
    return app


if __name__ == "__main__":
    tmp = tempfile.mkdtemp()
    try:
        pending = Path(tmp) / "directives" / "pending"
        proposed = Path(tmp) / "directives" / "proposed"
        pending.mkdir(parents=True)
        proposed.mkdir(parents=True)

        (pending / "fresh_directive.json").write_text(json.dumps({
            "task": "test_task",
            "handler": "TestHandler",
            "description": "Fresh directive"
        }))

        old_file = pending / "stale_directive.json"
        old_file.write_text(json.dumps({
            "task": "old_task",
            "handler": "OldHandler",
            "description": "Stale directive"
        }))
        old_mtime = time.time() - (100 * 3600)
        os.utime(old_file, (old_mtime, old_mtime))

        app = create_app(Path(tmp))
        client = TestClient(app)
        resp = client.get("/api/directives/health")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        data = resp.json()
        stalled = data.get("stalled", [])
        assert len(stalled) == 1, f"Expected 1 stalled, got {len(stalled)}"
        assert "TestHandler" in data.get("by_handler", {}), \
            f"Expected TestHandler in by_handler, got {data.get('by_handler')}"

        print("PASS")
    finally:
        shutil.rmtree(tmp)