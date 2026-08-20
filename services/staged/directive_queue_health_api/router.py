"""directive_queue_health_api router"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List
import requests

from app.db import get_session

router = APIRouter(prefix="/api", tags=["directive_queue_health"])


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
    """Query the write_service /query endpoint."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql},
        timeout=30
    )
    response.raise_for_status()
    return response.json()


def get_directive_counts() -> tuple[int, int]:
    """Get proposed and pending directive counts from write_service."""
    proposed_sql = "SELECT COUNT(*) as count FROM directives/proposed"
    pending_sql = "SELECT COUNT(*) as count FROM directives/pending"
    
    proposed_result = query_write_service(proposed_sql)
    pending_result = query_write_service(pending_sql)
    
    proposed_count = proposed_result.get("rows", [{}])[0].get("count", 0) if proposed_result.get("rows") else 0
    pending_count = pending_result.get("rows", [{}])[0].get("count", 0) if pending_result.get("rows") else 0
    
    return proposed_count, pending_count


def get_generator_status() -> str:
    """Get sentinel_directive_generator liveness status."""
    health_sql = "SELECT status FROM service_health WHERE service_name = 'sentinel_directive_generator'"
    try:
        result = query_write_service(health_sql)
        if result.get("rows"):
            return result["rows"][0].get("status", "unknown")
    except Exception:
        pass
    return "unknown"


def get_stale_daemons() -> List[StaleDaemon]:
    """Get list of stale daemons from service_health."""
    stale_sql = """
        SELECT name, age_seconds 
        FROM service_health 
        WHERE age_seconds > 300 AND service_type = 'daemon'
    """
    try:
        result = query_write_service(stale_sql)
        return [
            StaleDaemon(name=row["name"], age_seconds=row["age_seconds"])
            for row in result.get("rows", [])
        ]
    except Exception:
        return []


def calculate_queue_capacity(proposed: int, pending: int) -> float:
    """Calculate queue capacity percentage (assuming max 1000)."""
    max_capacity = 1000
    current = proposed + pending
    return min(100.0, round((current / max_capacity) * 100, 2))


@router.get("/internal/directive-queue/health", response_model=DirectiveQueueHealthResponse)
async def health():
    """Get directive queue health metrics."""
    proposed_count, pending_count = get_directive_counts()
    generator_status = get_generator_status()
    stale_daemons = get_stale_daemons()
    queue_capacity_pct = calculate_queue_capacity(proposed_count, pending_count)
    
    return DirectiveQueueHealthResponse(
        proposed_count=proposed_count,
        pending_count=pending_count,
        generator_status=generator_status,
        stale_daemons=stale_daemons,
        queue_capacity_pct=queue_capacity_pct
    )


if __name__ == "__main__":
    import unittest.mock as mock
    
    from fastapi import FastAPI
    
    # Mock write_service responses
    proposed_response = {"rows": [{"count": 3}]}
    pending_response = {"rows": [{"count": 2}]}
    stale_daemons_response = {
        "rows": [
            {"name": "daemon_1", "age_seconds": 600},
            {"name": "daemon_2", "age_seconds": 900}
        ]
    }
    status_response = {"rows": [{"status": "healthy"}]}
    
    def mock_post(url, **kwargs):
        mock_response = mock.MagicMock()
        sql = kwargs.get("json", {}).get("sql", "")
        
        if "directives/proposed" in sql:
            mock_response.json.return_value = proposed_response
        elif "directives/pending" in sql:
            mock_response.json.return_value = pending_response
        elif "service_name = 'sentinel_directive_generator'" in sql:
            mock_response.json.return_value = status_response
        elif "age_seconds > 300" in sql:
            mock_response.json.return_value = stale_daemons_response
        else:
            mock_response.json.return_value = {"rows": []}
        
        mock_response.raise_for_status = mock.MagicMock()
        return mock_response
    
    with mock.patch("requests.post", side_effect=mock_post):
        app = FastAPI()
        app.include_router(router)
        
        client = app.router  # get test client
        from fastapi.testclient import TestClient
        with TestClient(app) as tc:
            response = tc.get("/api/internal/directive-queue/health")
            
            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            data = response.json()
            assert len(data["stale_daemons"]) >= 1, f"Expected stale_daemons >= 1, got {len(data['stale_daemons'])}"
            
            print("PASS")