from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
import requests
from app.db import get_session

router = APIRouter()


class GeneratorHealth(BaseModel):
    name: str
    last_heartbeat_age_seconds: int
    stale: bool
    pending_directive_count: int


class QueueHealthResponse(BaseModel):
    generators: List[GeneratorHealth]
    overall_healthy: bool
    stalled_generators: List[str]


def get_stale_threshold() -> int:
    return 300


def get_pending_directives_dir() -> str:
    return "/var/lib/directives/pending"


def get_proposed_directives_dir() -> str:
    return "/var/lib/directives/proposed"


def get_service_health_rows() -> List[dict]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={
            "sql": "SELECT daemon_name, last_heartbeat, status FROM service_health WHERE service_type = 'directive_generator'",
            "params": {}
        },
        timeout=10
    )
    response.raise_for_status()
    result = response.json()
    return result.get("rows", [])


def get_pending_directive_count(daemon_name: str) -> int:
    import os
    pending_dir = get_pending_directives_dir()
    proposed_dir = get_proposed_directives_dir()
    count = 0
    for directory in [pending_dir, proposed_dir]:
        if os.path.isdir(directory):
            daemon_dir = os.path.join(directory, daemon_name)
            if os.path.isdir(daemon_dir):
                count += len([f for f in os.listdir(daemon_dir) if os.path.isfile(os.path.join(daemon_dir, f))])
    return count


def compute_queue_health() -> QueueHealthResponse:
    stale_threshold = get_stale_threshold()
    rows = get_service_health_rows()
    import time
    current_time = int(time.time())
    generators = []
    stalled = []
    for row in rows:
        daemon_name = row.get("daemon_name")
        last_heartbeat = row.get("last_heartbeat")
        if last_heartbeat is None:
            continue
        heartbeat_age = current_time - int(last_heartbeat)
        stale = heartbeat_age > stale_threshold
        pending_count = get_pending_directive_count(daemon_name)
        generators.append(GeneratorHealth(
            name=daemon_name,
            last_heartbeat_age_seconds=heartbeat_age,
            stale=stale,
            pending_directive_count=pending_count
        ))
        if stale:
            stalled.append(daemon_name)
    return QueueHealthResponse(
        generators=generators,
        overall_healthy=len(stalled) == 0,
        stalled_generators=stalled
    )


@router.get("/api/directives/queue-health", response_model=QueueHealthResponse)
def get_queue_health():
    return compute_queue_health()


if __name__ == "__main__":
    import sys
    import time
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    stale_daemon_name = "generator-alpha"
    healthy_daemon_name = "generator-beta"
    current_ts = int(time.time())
    stale_threshold = 300

    mock_service_health_response = {
        "rows": [
            {"daemon_name": stale_daemon_name, "last_heartbeat": current_ts - 600, "status": "running"},
            {"daemon_name": healthy_daemon_name, "last_heartbeat": current_ts - 60, "status": "running"},
        ]
    }

    def mock_post(url, json, timeout=None):
        resp = MagicMock()
        if "8772/query" in url:
            resp.status_code = 200
            resp.json.return_value = mock_service_health_response
        resp.raise_for_status = MagicMock()
        return resp

    mock_os_isdir = MagicMock(return_value=False)
    mock_os_listdir = MagicMock(return_value=[])

    with patch("requests.post", mock_post):
        with patch("os.path.isdir", mock_os_isdir):
            with patch("os.listdir", mock_os_listdir):
                with patch("os.path.isfile", return_value=False):
                    client = TestClient(app)
                    response = client.get("/api/directives/queue-health")
                    data = response.json()

    assert data["overall_healthy"] is False, f"Expected overall_healthy=False, got {data['overall_healthy']}"
    assert stale_daemon_name in data["stalled_generators"], f"Expected '{stale_daemon_name}' in stalled_generators, got {data['stalled_generators']}"
    assert healthy_daemon_name not in data["stalled_generators"], f"Expected '{healthy_daemon_name}' NOT in stalled_generators"
    print("PASS")