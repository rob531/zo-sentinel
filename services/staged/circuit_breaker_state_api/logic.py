"""
Circuit Breaker State API - reads Gate 8 circuit breaker health from write_service.
"""
import json
from datetime import datetime
from typing import Optional

import requests
from pydantic import BaseModel


class QuarantinedFile(BaseModel):
    file: str
    quarantined_at: Optional[str]
    consecutive_fails: int
    last_error: Optional[str]


class CircuitBreakerState(BaseModel):
    breaker_state: str
    trip_reason: Optional[str]
    trip_timestamp: Optional[str]
    quarantine_count: int
    quarantined_files: list[QuarantinedFile]


def get_circuit_breaker_state() -> CircuitBreakerState:
    """
    Query write_service for Gate 8 circuit breaker health state.
    Returns current breaker status and quarantine list.
    """
    query = {
        "table": "service_health",
        "filters": {"service_name": "circuit_breaker_gate_8"},
        "limit": 1
    }
    
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=2
        )
        resp.raise_for_status()
        results = resp.json()
        
        if not results or len(results) == 0:
            return CircuitBreakerState(
                breaker_state="ok",
                trip_reason=None,
                trip_timestamp=None,
                quarantine_count=0,
                quarantined_files=[]
            )
        
        record = results[0]
        metadata = record.get("metadata", {})
        
        breaker_state = "ok"
        trip_reason = None
        trip_timestamp = None
        quarantine_list = []
        
        if record.get("status") == "tripped":
            breaker_state = "tripped"
            trip_reason = record.get("error_message") or metadata.get("trip_reason")
            trip_timestamp = record.get("updated_at") or metadata.get("trip_timestamp")
        
        quarantined = metadata.get("quarantine_list", [])
        for q in quarantined:
            quarantine_list.append(QuarantinedFile(
                file=q.get("file", ""),
                quarantined_at=q.get("quarantined_at"),
                consecutive_fails=q.get("consecutive_fails", 0),
                last_error=q.get("last_error")
            ))
        
        return CircuitBreakerState(
            breaker_state=breaker_state,
            trip_reason=trip_reason,
            trip_timestamp=trip_timestamp,
            quarantine_count=len(quarantine_list),
            quarantined_files=quarantine_list
        )
        
    except requests.RequestException:
        return CircuitBreakerState(
            breaker_state="ok",
            trip_reason=None,
            trip_timestamp=None,
            quarantine_count=0,
            quarantined_files=[]
        )


if __name__ == "__main__":
    import sys
    
    from unittest.mock import MagicMock, patch
    
    mock_records = [{
        "service_name": "circuit_breaker_gate_8",
        "status": "tripped",
        "error_message": "Too many consecutive failures",
        "updated_at": "2024-01-15T10:30:00Z",
        "metadata": {
            "trip_reason": "Threshold exceeded",
            "trip_timestamp": "2024-01-15T10:30:00Z",
            "quarantine_list": [
                {
                    "file": "core_agent.py",
                    "quarantined_at": "2024-01-15T10:25:00Z",
                    "consecutive_fails": 5,
                    "last_error": "TimeoutError"
                },
                {
                    "file": "task_router.py",
                    "quarantined_at": "2024-01-15T10:28:00Z",
                    "consecutive_fails": 3,
                    "last_error": "ConnectionError"
                },
                {
                    "file": "executor_pool.py",
                    "quarantined_at": "2024-01-15T10:29:00Z",
                    "consecutive_fails": 4,
                    "last_error": "MemoryError"
                }
            ]
        }
    }]
    
    mock_response = MagicMock()
    mock_response.json.return_value = mock_records
    mock_response.raise_for_status = MagicMock()
    
    with patch("requests.post") as mock_post:
        mock_post.return_value = mock_response
        
        result = get_circuit_breaker_state()
        
        assert result.breaker_state == "tripped", f"Expected tripped, got {result.breaker_state}"
        assert result.quarantine_count == 3, f"Expected 3, got {result.quarantine_count}"
        assert any(f.file == "core_agent.py" for f in result.quarantined_files), "core_agent.py not found"
    
    print("PASS")