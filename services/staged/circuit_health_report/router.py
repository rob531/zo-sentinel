# services/staged/circuit_health_report/logic.py
from typing import Optional
import httpx
from datetime import datetime, timezone

from .schemas import CircuitHealthReport, BreakerState, ServiceStatus, RetryBudgetEntry, QuarantinedFile

APP_DB_URL = "http://127.0.0.1:8772"


def _read_quality_gate_state() -> dict:
    """Read the quality gate state from the write service."""
    query = """
    SELECT key, value FROM mesh_memory 
    WHERE key IN ('circuit_breaker_state', 'retry_budget', 'quarantine_list')
    """
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(APP_DB_URL, json={"sql": query})
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return {}


def _get_circuit_breaker_state() -> BreakerState:
    """Get the circuit breaker state from quality gate."""
    gate = _read_quality_gate_state()
    breaker_data = gate.get("circuit_breaker_state", {})
    return BreakerState(
        tripped=breaker_data.get("tripped", False),
        since=breaker_data.get("since")
    )


def _get_service_statuses() -> list[ServiceStatus]:
    """Get service statuses from the server registry."""
    query = "SELECT name, last_heartbeat FROM mcp_server_registry LIMIT 100"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(APP_DB_URL, json={"sql": query})
            if resp.status_code == 200:
                rows = resp.json()
                return [
                    ServiceStatus(
                        name=row["name"],
                        age_seconds=int((datetime.now(timezone.utc) - datetime.fromisoformat(row["last_heartbeat"].replace("Z", "+00:00"))).total_seconds()) if row.get("last_heartbeat") else 0,
                        status="healthy"
                    )
                    for row in rows
                ]
    except Exception:
        pass
    return []


def _get_retry_budget() -> list[RetryBudgetEntry]:
    """Get retry budget from quality gate state."""
    gate = _read_quality_gate_state()
    budget_data = gate.get("retry_budget", [])
    return [
        RetryBudgetEntry(
            file=entry.get("file", ""),
            attempts=entry.get("attempts", 0),
            max_attempts=entry.get("max_attempts", 3),
            last_error=entry.get("last_error")
        )
        for entry in budget_data
    ]


def _get_quarantined_files() -> list[QuarantinedFile]:
    """Get quarantined files from quality gate state."""
    gate = _read_quality_gate_state()
    quarantine_data = gate.get("quarantine_list", [])
    return [
        QuarantinedFile(
            file=entry.get("file", ""),
            quarantined_at=entry.get("quarantined_at"),
            reason=entry.get("reason", "unknown")
        )
        for entry in quarantine_data
    ]


def get_circuit_health_report() -> CircuitHealthReport:
    """Build the complete circuit health report."""
    return CircuitHealthReport(
        breaker=_get_circuit_breaker_state(),
        services=_get_service_statuses(),
        retry_budget=_get_retry_budget(),
        quarantined=_get_quarantined_files()
    )