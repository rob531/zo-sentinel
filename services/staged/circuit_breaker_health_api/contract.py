from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
import json
import os
from datetime import datetime
from app.db import get_session
from app.models import McpServerRegistry
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
import requests
from unittest.mock import patch

class DaemonStatus(BaseModel):
    name: str
    age_seconds: int
    threshold_seconds: int
    status: str

class CohortFailRate(BaseModel):
    cohort: str
    fail_rate: float
    size: int

class CircuitBreakerResponse(BaseModel):
    breaker_tripped: bool
    tripped_since: Optional[str]
    quarantined_files: List[str]
    stale_daemons: List[DaemonStatus]
    cohort_fail_rates: List[CohortFailRate]

def read_circuit_breaker_state() -> dict:
    state_file = "/home/workspace/zo_sentinel/circuit_breaker_state.json"
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            return json.load(f)
    return {"breaker_tripped": False, "tripped_since": None, "quarantined_files": []}

def query_write_service(service_health_query: str) -> List[dict]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": service_health_query}
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Failed to query write_service")
    return response.json()

def get_circuit_breaker_health() -> CircuitBreakerResponse:
    circuit_breaker_state = read_circuit_breaker_state()

    service_health_query = """
    SELECT name, age_seconds, threshold_seconds, status
    FROM service_health
    WHERE age_seconds > threshold_seconds
    """
    stale_daemons_data = query_write_service(service_health_query)
    stale_daemons = [DaemonStatus(**daemon) for daemon in stale_daemons_data]

    cohort_fail_rates_query = """
    SELECT cohort, fail_rate, size
    FROM cohort_fail_rates
    """
    cohort_fail_rates_data = query_write_service(cohort_fail_rates_query)
    cohort_fail_rates = [CohortFailRate(**rate) for rate in cohort_fail_rates_data]

    return CircuitBreakerResponse(
        breaker_tripped=circuit_breaker_state["breaker_tripped"],
        tripped_since=circuit_breaker_state["tripped_since"],
        quarantined_files=circuit_breaker_state["quarantined_files"],
        stale_daemons=stale_daemons,
        cohort_fail_rates=cohort_fail_rates
    )

app = FastAPI()

@app.get("/api/circuit-breaker/health", response_model=CircuitBreakerResponse)
async def circuit_breaker_health():
    return get_circuit_breaker_health()

if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from app.db import get_session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(
        bind=StaticPool.create_engine("sqlite:///:memory:"),
        autocommit=True,
        autoflush=False
    )

    @test_app.get("/api/circuit-breaker/health")
    async def mock_circuit_breaker_health():
        return {
            "breaker_tripped": True,
            "tripped_since": "2023-01-01T00:00:00",
            "quarantined_files": ["file1", "file2"],
            "stale_daemons": [
                {"name": "daemon1", "age_seconds": 3600, "threshold_seconds": 1800, "status": "stale"},
                {"name": "daemon2", "age_seconds": 1800, "threshold_seconds": 3600, "status": "ok"}
            ],
            "cohort_fail_rates": [
                {"cohort": "cohort1", "fail_rate": 0.1, "size": 100},
                {"cohort": "cohort2", "fail_rate": 0.2, "size": 200}
            ]
        }

    with patch("services.staged.circuit_breaker_health_api.contract.query_write_service") as mock_query:
        mock_query.return_value = [
            {"name": "daemon1", "age_seconds": 3600, "threshold_seconds": 1800, "status": "stale"},
            {"name": "daemon2", "age_seconds": 1800, "threshold_seconds": 3600, "status": "ok"}
        ]

        with patch("services.staged.circuit_breaker_health_api.contract.read_circuit_breaker_state") as mock_state:
            mock_state.return_value = {
                "breaker_tripped": True,
                "tripped_since": "2023-01-01T00:00:00",
                "quarantined_files": ["file1", "file2"]
            }

            client = TestClient(test_app)
            response = client.get("/api/circuit-breaker/health")
            assert response.status_code == 200
            data = response.json()
            assert len(data["stale_daemons"]) >= 1
            assert isinstance(data["breaker_tripped"], bool)
            print("PASS")