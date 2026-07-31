import os
import json
import datetime
from typing import List, Dict, Optional

import requests
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from fastapi.testclient import TestClient

# Real application data layer import (required by the build system)
from app.db import get_session  # noqa: F401  (imported for side‑effects / contract)

router = APIRouter()


class DirectiveQueueHealthResponse(BaseModel):
    pending_tasks: List[str] = Field(..., description="List of pending task identifiers")
    proposed_tasks: List[str] = Field(..., description="List of proposed task identifiers")
    handler_counts: Dict[str, int] = Field(..., description="Aggregated count per handler")
    recent_failures: int = Field(..., description="Number of recent failure events")
    last_failure_at: Optional[str] = Field(
        None, description="ISO‑8601 timestamp of the most recent failure or null"
    )


def _load_tasks(base_dir: str) -> Dict[str, List[str]]:
    """
    Scan ``pending`` and ``proposed`` sub‑directories under ``base_dir``.
    Returns a mapping with keys ``pending`` and ``proposed`` containing the
    ``task`` field from each JSON file.
    """
    result = {"pending": [], "proposed": []}
    for category in ("pending", "proposed"):
        dir_path = os.path.join(base_dir, "directives", category)
        if not os.path.isdir(dir_path):
            continue
        for fname in os.listdir(dir_path):
            if not fname.lower().endswith(".json"):
                continue
            fpath = os.path.join(dir_path, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                task = data.get("task")
                if isinstance(task, str):
                    result[category].append(task)
            except Exception:
                # Silently ignore malformed files – they are not part of health metrics
                continue
    return result


def _aggregate_handler_counts(base_dir: str) -> Dict[str, int]:
    """
    Walk both ``pending`` and ``proposed`` directories and count occurrences of the
    top‑level ``handler`` field.
    """
    counts: Dict[str, int] = {}
    for category in ("pending", "proposed"):
        dir_path = os.path.join(base_dir, "directives", category)
        if not os.path.isdir(dir_path):
            continue
        for fname in os.listdir(dir_path):
            if not fname.lower().endswith(".json"):
                continue
            fpath = os.path.join(dir_path, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                handler = data.get("handler")
                if isinstance(handler, str):
                    counts[handler] = counts.get(handler, 0) + 1
            except Exception:
                continue
    return counts


def _query_recent_failures() -> Dict[str, Optional[object]]:
    """
    Query the mesh_memory table via the write‑service for failure‑type events
    in the last 48 hours. Returns a dict with ``count`` and ``last`` keys.
    """
    query_payload = {
        "sql": """
            SELECT
                COUNT(*) AS cnt,
                MAX(event_time) AS last
            FROM mesh_memory
            WHERE event_type IN ('build_failure', 'directive_generation')
              AND event_time >= (NOW() - INTERVAL '48 HOURS')
        """
    }
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query", json=query_payload, timeout=5
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if rows:
            row = rows[0]
            cnt = int(row.get("cnt", 0))
            last = row.get("last")
            if isinstance(last, str):
                # Ensure ISO‑8601 format; if not, attempt conversion
                try:
                    datetime.datetime.fromisoformat(last)
                except Exception:
                    last = None
            else:
                last = None
            return {"count": cnt, "last": last}
    except Exception:
        # On any error, fall back to zero failures and no timestamp
        pass
    return {"count": 0, "last": None}


@router.get(
    "/api/internal/directive/queue-health",
    response_model=DirectiveQueueHealthResponse,
    tags=["internal"],
)
async def get_queue_health(
    base_dir: str = Depends(lambda: os.getenv("DIRECTIVE_ROOT", ".")),
    _session=Depends(get_session),  # kept for contract compliance; not used here
):
    """
    Assemble health information for the directive queue.
    """
    tasks = _load_tasks(base_dir)
    handler_counts = _aggregate_handler_counts(base_dir)
    failures = _query_recent_failures()

    return DirectiveQueueHealthResponse(
        pending_tasks=tasks.get("pending", []),
        proposed_tasks=tasks.get("proposed", []),
        handler_counts=handler_counts,
        recent_failures=failures["count"],
        last_failure_at=failures["last"],
    )


if __name__ == "__main__":
    # ----------------------------------------------------------------------
    # Self‑test using FastAPI's TestClient and a temporary filesystem layout.
    # ----------------------------------------------------------------------
    import tempfile
    from unittest.mock import patch

    # Create a temporary directory structure with fake directive JSON files
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "directives", "pending"))
        os.makedirs(os.path.join(tmpdir, "directives", "proposed"))

        # Three directive files: two pending, one proposed
        directives = [
            ("pending", "task1.json", {"task": "task1", "handler": "alpha"}),
            ("pending", "task2.json", {"task": "task2", "handler": "beta"}),
            ("proposed", "task3.json", {"task": "task3", "handler": "alpha"}),
        ]

        for category, fname, payload in directives:
            fpath = os.path.join(tmpdir, "directives", category, fname)
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(payload, f)

        # Mock the external write_service query
        mock_response = {
            "rows": [
                {
                    "cnt": 7,
                    "last": datetime.datetime.utcnow()
                    .replace(microsecond=0)
                    .isoformat()
                    + "Z",
                }
            ]
        }

        def mock_post(*_, **kwargs):
            class MockResp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return mock_response

            return MockResp()

        # Build FastAPI app with the router
        app = FastAPI()
        app.include_router(router)

        # Override the get_session dependency with a dummy (no DB needed)
        def dummy_session():
            return None

        app.dependency_overrides[get_session] = dummy_session

        # Set environment variable so the endpoint sees our temp directory
        os.environ["DIRECTIVE_ROOT"] = tmpdir

        with patch("requests.post", new=mock_post):
            client = TestClient(app)
            resp = client.get("/api/internal/directive/queue-health")
            assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
            data = resp.json()

            # Validate handler_counts sum equals total number of directives (3)
            total_handlers = sum(data.get("handler_counts", {}).values())
            assert total_handlers == 3, f"Handler count mismatch: {total_handlers}"

            # recent_failures must be an integer
            assert isinstance(data.get("recent_failures"), int), "recent_failures not int"

            # Print PASS if all assertions succeed
            print("PASS")