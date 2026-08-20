from fastapi import Depends
from pydantic import BaseModel
from typing import List
import requests
from app.db import get_session
from sqlalchemy.orm import Session

app = None

class StaleDaemon(BaseModel):
    name: str
    age_seconds: int

class DirectiveQueueHealthResponse(BaseModel):
    proposed_count: int
    pending_count: int
    generator_status: str
    stale_daemons: List[StaleDaemon]
    queue_capacity_pct: float

def query_write_service(sql: str) -> dict:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql},
        timeout=30
    )
    response.raise_for_status()
    return response.json()

def get_directive_counts() -> dict:
    proposed_result = query_write_service(
        "SELECT COUNT(*) as count FROM directives WHERE status = 'proposed'"
    )
    pending_result = query_write_service(
        "SELECT COUNT(*) as count FROM directives WHERE status = 'pending'"
    )
    return {
        "proposed_count": proposed_result[0]["count"] if proposed_result else 0,
        "pending_count": pending_result[0]["count"] if pending_result else 0
    }

def get_stale_daemons() -> List[StaleDaemon]:
    stale_result = query_write_service(
        "SELECT name, EXTRACT(EPOCH FROM (NOW() - last_heartbeat)) as age_seconds "
        "FROM service_health WHERE last_heartbeat < NOW() - INTERVAL '60 seconds'"
    )
    return [StaleDaemon(name=r["name"], age_seconds=int(r["age_seconds"])) for r in stale_result]

def get_generator_status() -> str:
    health_result = query_write_service(
        "SELECT status, last_heartbeat FROM service_health WHERE service_name = 'sentinel_directive_generator'"
    )
    if not health_result:
        return "unknown"
    row = health_result[0]
    status = row.get("status", "unknown")
    heartbeat = row.get("last_heartbeat")
    if heartbeat:
        age = query_write_service(
            "SELECT EXTRACT(EPOCH FROM (NOW() - last_heartbeat)) as age FROM service_health WHERE service_name = 'sentinel_directive_generator'"
        )
        if age and age[0]["age"] > 60:
            return "stale"
    return status if status else "unknown"

def health() -> DirectiveQueueHealthResponse:
    counts = get_directive_counts()
    generator_status = get_generator_status()
    stale_daemons = get_stale_daemons()
    total = counts["proposed_count"] + counts["pending_count"]
    queue_capacity_pct = (total / 10000) * 100
    return DirectiveQueueHealthResponse(
        proposed_count=counts["proposed_count"],
        pending_count=counts["pending_count"],
        generator_status=generator_status,
        stale_daemons=stale_daemons,
        queue_capacity_pct=queue_capacity_pct
    )

def get_app():
    global app
    if app is None:
        from fastapi import FastAPI
        app = FastAPI()
        @app.get("/api/internal/directive-queue/health", response_model=DirectiveQueueHealthResponse)
        async def get_directive_queue_health():
            return health()
    return app

if __name__ == "__main__":
    from unittest.mock import patch, MagicMock
    from fastapi.testclient import TestClient

    mock_proposed_result = [{"count": 3}]
    mock_pending_result = [{"count": 2}]
    mock_stale_result = [
        {"name": "daemon1", "age_seconds": 600},
        {"name": "daemon2", "age_seconds": 1200},
    ]
    mock_health_result = [{"status": "running", "last_heartbeat": "2024-01-01T00:00:00"}]
    mock_age_result = [{"age": 120}]

    def mock_post(url, **kwargs):
        m = MagicMock()
        m.raise_for_status = MagicMock()
        sql = kwargs.get("json", {}).get("sql", "")
        if "proposed" in sql:
            m.json.return_value = mock_proposed_result
        elif "pending" in sql:
            m.json.return_value = mock_pending_result
        elif "stale" in sql.lower() or "service_health" in sql.lower():
            m.json.return_value = mock_stale_result
        elif "sentinel_directive_generator" in sql and "age" in sql:
            m.json.return_value = mock_age_result
        elif "sentinel_directive_generator" in sql:
            m.json.return_value = mock_health_result
        return m

    with patch("requests.post", mock_post):
        client = TestClient(get_app())
        response = client.get("/api/internal/directive-queue/health")
        assert response.status_code == 200
        data = response.json()
        assert len(data["stale_daemons"]) >= 1
        print("PASS")