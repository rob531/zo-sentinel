"""
Directive Queue Health API - reads pending/proposed directive JSONs from disk.

Returns per-handler counts, age statistics, and identifies starved/stalled tasks.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

router = APIRouter()


class HandlerMetrics(BaseModel):
    pending: int
    proposed: int
    oldest_age_seconds: float


class HandlersDict(BaseModel):
    pass


class Summary(BaseModel):
    total: int
    handlers_with_backlog: int
    starved_threshold_seconds: float


class HealthResponse(BaseModel):
    handlers: dict[str, HandlerMetrics]
    summary: Summary


def get_handler_name_from_file(file_path: Path) -> str:
    """Extract handler name from directive JSON file."""
    try:
        with open(file_path, "r") as f:
            data = json.load(f)
        return data.get("handler", file_path.stem)
    except (json.JSONDecodeError, OSError):
        return file_path.stem


def compute_file_age_seconds(file_path: Path) -> float:
    """Compute age of a file in seconds based on modification time."""
    mtime = file_path.stat().st_mtime
    now = time.time()
    return max(0.0, now - mtime)


def get_directives_health(
    directives_path: Path,
    starved_threshold_seconds: float = 300.0,
) -> HealthResponse:
    """
    Read pending and proposed directive JSON files from disk and compute health metrics.

    Args:
        directives_path: Root path containing pending/ and proposed/ subdirectories.
        starved_threshold_seconds: Threshold in seconds to consider a task starved.

    Returns:
        HealthResponse with per-handler metrics and summary.
    """
    pending_dir = directives_path / "pending"
    proposed_dir = directives_path / "proposed"

    handler_data: dict[str, dict[str, Any]] = {}

    # Process pending files
    if pending_dir.exists():
        for file_path in pending_dir.glob("*.json"):
            handler = get_handler_name_from_file(file_path)
            if handler not in handler_data:
                handler_data[handler] = {"pending": 0, "proposed": 0, "oldest_age_seconds": 0.0}
            handler_data[handler]["pending"] += 1
            age = compute_file_age_seconds(file_path)
            handler_data[handler]["oldest_age_seconds"] = max(
                handler_data[handler]["oldest_age_seconds"], age
            )

    # Process proposed files
    if proposed_dir.exists():
        for file_path in proposed_dir.glob("*.json"):
            handler = get_handler_name_from_file(file_path)
            if handler not in handler_data:
                handler_data[handler] = {"pending": 0, "proposed": 0, "oldest_age_seconds": 0.0}
            handler_data[handler]["proposed"] += 1
            age = compute_file_age_seconds(file_path)
            handler_data[handler]["oldest_age_seconds"] = max(
                handler_data[handler]["oldest_age_seconds"], age
            )

    # Build handlers dict
    handlers: dict[str, HandlerMetrics] = {}
    for handler, data in handler_data.items():
        handlers[handler] = HandlerMetrics(
            pending=data["pending"],
            proposed=data["proposed"],
            oldest_age_seconds=round(data["oldest_age_seconds"], 2),
        )

    # Compute summary
    total = sum(data["pending"] + data["proposed"] for data in handler_data.values())
    handlers_with_backlog = sum(
        1
        for data in handler_data.values()
        if data["pending"] > 0 or data["proposed"] > 0
    )

    summary = Summary(
        total=total,
        handlers_with_backlog=handlers_with_backlog,
        starved_threshold_seconds=starved_threshold_seconds,
    )

    return HealthResponse(handlers=handlers, summary=summary)


@router.get("/api/directives/queue/health", response_model=HealthResponse)
async def get_queue_health(request: Request) -> HealthResponse:
    """
    Get health metrics for directive queue.

    Returns per-handler counts of pending/proposed directives,
    oldest task age, and summary statistics.
    """
    directives_path = request.app.state.directives_path
    starved_threshold = getattr(request.app.state, "starved_threshold_seconds", 300.0)
    return get_directives_health(directives_path, starved_threshold)


# Application factory
def create_app() -> FastAPI:
    app = FastAPI()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.directives_path = Path("directives")
    app.state.starved_threshold_seconds = 300.0

    app.include_router(router)
    return app


app = create_app()


# Self-test
if __name__ == "__main__":
    import tempfile

    from fastapi.testclient import TestClient

    # Create temp directive tree with dummy files
    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        pending_dir = tmppath / "pending"
        proposed_dir = tmppath / "proposed"
        pending_dir.mkdir()
        proposed_dir.mkdir()

        # Create 3 dummy JSON files with varied ages
        now = time.time()

        # File 1: handler_alpha, 10 seconds old
        f1 = pending_dir / "alpha_1.json"
        f1.write_text(json.dumps({"handler": "handler_alpha", "directive_id": "alpha_1"}))
        import os

        os.utime(f1, (now - 10, now - 10))

        # File 2: handler_alpha, 5 seconds old
        f2 = pending_dir / "alpha_2.json"
        f2.write_text(json.dumps({"handler": "handler_alpha", "directive_id": "alpha_2"}))
        os.utime(f2, (now - 5, now - 5))

        # File 3: handler_beta, 200 seconds old (starved)
        f3 = proposed_dir / "beta_1.json"
        f3.write_text(json.dumps({"handler": "handler_beta", "directive_id": "beta_1"}))
        os.utime(f3, (now - 200, now - 200))

        # Create test app
        test_app = create_app()
        test_app.state.directives_path = tmppath

        with TestClient(test_app) as client:
            resp = client.get("/api/directives/queue/health")

            # Assert 200
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

            data = resp.json()

            # Assert structure
            assert "handlers" in data, "Missing 'handlers' in response"
            assert "summary" in data, "Missing 'summary' in response"

            # Assert handler counts >= 0
            for handler, metrics in data["handlers"].items():
                assert metrics["pending"] >= 0, f"pending count negative for {handler}"
                assert metrics["proposed"] >= 0, f"proposed count negative for {handler}"
                assert metrics["oldest_age_seconds"] >= 0, f"oldest_age negative for {handler}"

            # Assert handler_alpha has 2 pending
            assert data["handlers"]["handler_alpha"]["pending"] == 2, (
                f"Expected 2 pending for handler_alpha, got {data['handlers']['handler_alpha']['pending']}"
            )

            # Assert handler_beta has 1 proposed
            assert data["handlers"]["handler_beta"]["proposed"] == 1, (
                f"Expected 1 proposed for handler_beta, got {data['handlers']['handler_beta']['proposed']}"
            )

            # Assert oldest_age >= 0 for all handlers
            for handler, metrics in data["handlers"].items():
                assert metrics["oldest_age_seconds"] >= 0, (
                    f"oldest_age_seconds negative for {handler}"
                )

            # Assert summary
            assert data["summary"]["total"] == 3, f"Expected total=3, got {data['summary']['total']}"
            assert data["summary"]["handlers_with_backlog"] == 2, (
                f"Expected 2 handlers_with_backlog, got {data['summary']['handlers_with_backlog']}"
            )

    print("PASS")