import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests
from fastapi import APIRouter, FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel

# --- Configuration Constants ---
CORE_DAEMONS = [
    "mcp_scanner",
    "write_service",
    "gate_scheduler",
    "rug_pull_monitor",
    "anti_entropy",
    "wisdom_synthesiser",
]

# URL for the internal write_service endpoint that provides health data
# In a real deployment, this would be a discoverable service URL.
WRITE_SERVICE_HEALTH_URL = "http://write_service:8000/api/v1/internal/service_health"

# Health thresholds in seconds
HEALTHY_THRESHOLD_SECONDS = 60  # Heartbeat within this time is HEALTHY
DEGRADED_THRESHOLD_SECONDS = 300  # Heartbeat older than this is CRITICAL, otherwise DEGRADED

# --- Pydantic Models ---

class ServiceHealthRecord(BaseModel):
    """
    Model for a single service health record as returned by write_service.
    """
    daemon_name: str
    last_heartbeat: datetime

class DaemonHealth(BaseModel):
    """
    Model for the health status of an individual daemon.
    """
    last_heartbeat: Optional[datetime]
    status: str  # e.g., "HEALTHY", "DEGRADED", "CRITICAL", "UNKNOWN"

class PipelineHealthResponse(BaseModel):
    """
    Model for the aggregated pipeline health response.
    """
    summary: str  # "HEALTHY", "DEGRADED", "CRITICAL"
    daemons: Dict[str, DaemonHealth]

# --- FastAPI Router ---

router = APIRouter()

@router.get(
    "/api/v1/health/pipeline",
    response_model=PipelineHealthResponse,
    summary="Aggregates health status of core daemons",
    response_description="Aggregated health status of the pipeline daemons"
)
async def get_pipeline_health():
    """
    Retrieves and aggregates the health status of core daemons
    (mcp_scanner, write_service, gate_scheduler, rug_pull_monitor,
    anti_entropy, wisdom_synthesiser).

    It queries the `write_service` for the `service_health` table data
    and determines the status of each daemon based on its `last_heartbeat`.
    The overall pipeline summary status is derived from individual daemon statuses.

    Returns:
        A JSON object with a summary status (HEALTHY/DEGRADED/CRITICAL)
        and a per-daemon breakdown of last_heartbeat and status.
    """
    daemon_statuses: Dict[str, DaemonHealth] = {}
    overall_summary_status = "HEALTHY"
    now_utc = datetime.now(timezone.utc)

    try:
        response = requests.get(WRITE_SERVICE_HEALTH_URL, timeout=5)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        service_health_data = [ServiceHealthRecord(**record) for record in response.json()]
        
        # Convert list to a dict for easier lookup
        health_data_map = {record.daemon_name: record for record in service_health_data}

        for daemon_name in CORE_DAEMONS:
            daemon_record = health_data_map.get(daemon_name)
            
            if not daemon_record or not daemon_record.last_heartbeat:
                # Daemon not found in the response or heartbeat is missing
                daemon_statuses[daemon_name] = DaemonHealth(last_heartbeat=None, status="UNKNOWN")
                if overall_summary_status == "HEALTHY": # UNKNOWN is worse than HEALTHY
                    overall_summary_status = "DEGRADED" 
                continue

            # Ensure last_heartbeat is timezone-aware for comparison
            last_heartbeat_aware = daemon_record.last_heartbeat.astimezone(timezone.utc)
            time_since_heartbeat = (now_utc - last_heartbeat_aware).total_seconds()

            if time_since_heartbeat < HEALTHY_THRESHOLD_SECONDS:
                status_str = "HEALTHY"
            elif time_since_heartbeat < DEGRADED_THRESHOLD_SECONDS:
                status_str = "DEGRADED"
                if overall_summary_status == "HEALTHY":
                    overall_summary_status = "DEGRADED"
            else:
                status_str = "CRITICAL"
                overall_summary_status = "CRITICAL" # CRITICAL overrides all

            daemon_statuses[daemon_name] = DaemonHealth(
                last_heartbeat=daemon_record.last_heartbeat,
                status=status_str
            )

    except requests.exceptions.Timeout:
        # write_service did not respond within the timeout
        overall_summary_status = "CRITICAL"
        for daemon_name in CORE_DAEMONS:
            daemon_statuses[daemon_name] = DaemonHealth(last_heartbeat=None, status="CRITICAL")
        # Optionally, raise an HTTPException if we want to signal the upstream caller
        # raise HTTPException(
        #     status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        #     detail="write_service is unreachable or timed out."
        # )
    except requests.exceptions.RequestException as e:
        # General request error (e.g., connection error, DNS error, bad status code)
        overall_summary_status = "CRITICAL"
        for daemon_name in CORE_DAEMONS:
            daemon_statuses[daemon_name] = DaemonHealth(last_heartbeat=None, status="CRITICAL")
        # raise HTTPException(
        #     status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        #     detail=f"Failed to query write_service: {e}"
        # )
    except Exception as e:
        # Catch any other unexpected errors during processing
        overall_summary_status = "CRITICAL"
        for daemon_name in CORE_DAEMONS:
            daemon_statuses[daemon_name] = DaemonHealth(last_heartbeat=None, status="CRITICAL")
        # raise HTTPException(
        #     status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        #     detail=f"An unexpected error occurred: {e}"
        # )

    return PipelineHealthResponse(
        summary=overall_summary_status,
        daemons=daemon_statuses
    )

# --- Main Application for Testing ---

if __name__ == "__main__":
    from unittest.mock import patch, Mock

    app = FastAPI(title="Pipeline Health Monitor API")
    app.include_router(router)
    client = TestClient(app)

    print("Running acceptance tests for pipeline_health_monitor_api.py...")

    # Helper to create mock health records
    def create_mock_health_data(
        daemon_name: str,
        seconds_ago: int,
        now: datetime
    ) -> ServiceHealthRecord:
        return ServiceHealthRecord(
            daemon_name=daemon_name,
            last_heartbeat=now - timedelta(seconds=seconds_ago)
        )

    # Mock the requests.get call
    with patch('requests.get') as mock_requests_get:
        # --- Test Case 1: All Daemons Healthy ---
        print("\n--- Test Case 1: All Daemons Healthy ---")
        mock_now_1 = datetime.now(timezone.utc)
        healthy_data = [
            create_mock_health_data(d, 10, mock_now_1) for d in CORE_DAEMONS
        ]
        mock_response_1 = Mock()
        mock_response_1.status_code = 200
        mock_response_1.json.return_value = [d.model_dump(mode='json') for d in healthy_data]
        mock_requests_get.return_value = mock_response_1

        response = client.get("/api/v1/health/pipeline")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["summary"] == "HEALTHY", f"Expected summary HEALTHY, got {data['summary']}"
        for daemon in CORE_DAEMONS:
            assert data["daemons"][daemon]["status"] == "HEALTHY", \
                f"Expected {daemon} status HEALTHY, got {data['daemons'][daemon]['status']}"
            assert data["daemons"][daemon]["last_heartbeat"] is not None
        print("PASS: All daemons healthy scenario.")

        # --- Test Case 2: One Daemon Degraded ---
        print("\n--- Test Case 2: One Daemon Degraded ---")
        mock_now_2 = datetime.now(timezone.utc)
        degraded_daemon = "gate_scheduler"
        degraded_data = [
            create_mock_health_data(d, 10 if d != degraded_daemon else 120, mock_now_2)
            for d in CORE_DAEMONS
        ]
        mock_response_2 = Mock()
        mock_response_2.status_code = 200
        mock_response_2.json.return_value = [d.model_dump(mode='json') for d in degraded_data]
        mock_requests_get.return_value = mock_response_2

        response = client.get("/api/v1/health/pipeline")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["summary"] == "DEGRADED", f"Expected summary DEGRADED, got {data['summary']}"
        assert data["daemons"][degraded_daemon]["status"] == "DEGRADED", \
            f"Expected {degraded_daemon} status DEGRADED, got {data['daemons'][degraded_daemon]['status']}"
        for daemon in CORE_DAEMONS:
            if daemon != degraded_daemon:
                assert data["daemons"][daemon]["status"] == "HEALTHY", \
                    f"Expected {daemon} status HEALTHY, got {data['daemons'][daemon]['status']}"
            assert data["daemons"][daemon]["last_heartbeat"] is not None
        print("PASS: One daemon degraded scenario.")

        # --- Test Case 3: One Daemon Critical ---
        print("\n--- Test Case 3: One Daemon Critical ---")
        mock_now_3 = datetime.now(timezone.utc)
        critical_daemon = "rug_pull_monitor"
        critical_data = [
            create_mock_health_data(d, 10 if d != critical_daemon else 350, mock_now_3)
            for d in CORE_DAEMONS
        ]
        mock_response_3 = Mock()
        mock_response_3.status_code = 200
        mock_response_3.json.return_value = [d.model_dump(mode='json') for d in critical_data]
        mock_requests_get.return_value = mock_response_3

        response = client.get("/api/v1/health/pipeline")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["summary"] == "CRITICAL", f"Expected summary CRITICAL, got {data['summary']}"
        assert data["daemons"][critical_daemon]["status"] == "CRITICAL", \
            f"Expected {critical_daemon} status CRITICAL, got {data['daemons'][critical_daemon]['status']}"
        for daemon in CORE_DAEMONS:
            if daemon != critical_daemon:
                assert data["daemons"][daemon]["status"] == "HEALTHY", \
                    f"Expected {daemon} status HEALTHY, got {data['daemons'][daemon]['status']}"
            assert data["daemons"][daemon]["last_heartbeat"] is not None
        print("PASS: One daemon critical scenario.")

        # --- Test Case 4: write_service is down (RequestException) ---
        print("\n--- Test Case 4: write_service is down (RequestException) ---")
        mock_requests_get.side_effect = requests.exceptions.ConnectionError("Mock connection error")

        response = client.get("/api/v1/health/pipeline")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["summary"] == "CRITICAL", f"Expected summary CRITICAL, got {data['summary']}"
        for daemon in CORE_DAEMONS:
            assert data["daemons"][daemon]["status"] == "CRITICAL", \
                f"Expected {daemon} status CRITICAL, got {data['daemons'][daemon]['status']}"
            assert data["daemons"][daemon]["last_heartbeat"] is None
        print("PASS: write_service down scenario.")
        mock_requests_get.side_effect = None # Reset side effect

        # --- Test Case 5: write_service returns empty list (no daemons reported) ---
        print("\n--- Test Case 5: write_service returns empty list ---")
        mock_response_5 = Mock()
        mock_response_5.status_code = 200
        mock_response_5.json.return_value = []
        mock_requests_get.return_value = mock_response_5

        response = client.get("/api/v1/health/pipeline")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["summary"] == "DEGRADED", f"Expected summary DEGRADED, got {data['summary']}"
        for daemon in CORE_DAEMONS:
            assert data["daemons"][daemon]["status"] == "UNKNOWN", \
                f"Expected {daemon} status UNKNOWN, got {data['daemons'][daemon]['status']}"
            assert data["daemons"][daemon]["last_heartbeat"] is None
        print("PASS: write_service returns empty list scenario.")

        # --- Test Case 6: One daemon missing from write_service response ---
        print("\n--- Test Case 6: One daemon missing from write_service response ---")
        mock_now_6 = datetime.now(timezone.utc)
        missing_daemon = "anti_entropy"
        partial_data = [
            create_mock_health_data(d, 10, mock_now_6) for d in CORE_DAEMONS if d != missing_daemon
        ]
        mock_response_6 = Mock()
        mock_response_6.status_code = 200
        mock_response_6.json.return_value = [d.model_dump(mode='json') for d in partial_data]
        mock_requests_get.return_value = mock_response_6

        response = client.get("/api/v1/health/pipeline")
        assert response.status_code == 200, f"Expected 200, got {response.status_code}"
        data = response.json()
        assert data["summary"] == "DEGRADED", f"Expected summary DEGRADED, got {data['summary']}"
        assert data["daemons"][missing_daemon]["status"] == "UNKNOWN", \
            f"Expected {missing_daemon} status UNKNOWN, got {data['daemons'][missing_daemon]['status']}"
        assert data["daemons"][missing_daemon]["last_heartbeat"] is None
        for daemon in CORE_DAEMONS:
            if daemon != missing_daemon:
                assert data["daemons"][daemon]["status"] == "HEALTHY", \
                    f"Expected {daemon} status HEALTHY, got {data['daemons'][daemon]['status']}"
            assert data["daemons"][daemon]["last_heartbeat"] is not None
        print("PASS: One daemon missing from response scenario.")

    print("\nAll acceptance tests passed!")