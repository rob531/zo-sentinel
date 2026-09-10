from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

router = APIRouter(prefix="/api")


class GateHealth(BaseModel):
    breaker_state: str
    tripped_at: Optional[str] = None
    cohort_info: Dict[str, Any] = {}


class QuarantineEntry(BaseModel):
    file: str
    quarantined_at: Optional[str] = None
    fail_count: int = 0
    last_error: Optional[str] = None


class QuarantineList(BaseModel):
    entries: List[QuarantineEntry]
    total: int


# Circuit breaker state
class CircuitBreakerState:
    def __init__(self):
        self._state = {
            "breaker_state": "closed",
            "quarantine": {},
            "retry_budgets": {}
        }


circuit_breaker = CircuitBreakerState()


def get_gate_health() -> GateHealth:
    state = circuit_breaker._state
    return GateHealth(
        breaker_state=state["breaker_state"],
        tripped_at=None,
        cohort_info={}
    )


def get_quarantine_list() -> QuarantineList:
    state = circuit_breaker._state
    entries = [
        QuarantineEntry(
            file=file_path,
            quarantined_at=qdata.get("quarantined_at"),
            fail_count=qdata.get("fail_count", 0),
            last_error=qdata.get("last_error")
        )
        for file_path, qdata in state["quarantine"].items()
    ]
    return QuarantineList(entries=entries, total=len(entries))


@router.get("/gate/health", response_model=GateHealth)
def gate_health():
    return get_gate_health()


@router.get("/gate/quarantine", response_model=QuarantineList)
def gate_quarantine():
    return get_quarantine_list()


def get_service_health_status():
    return get_gate_health()


def ensure_service_health():
    return get_gate_health()


def send_heartbeat():
    return get_gate_health()


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        yield TestingSessionLocal()

    app.dependency_overrides[None] = override_get_session

    circuit_breaker._state = {
        "breaker_state": "tripped",
        "quarantine": {
            "/tmp/failing_endpoint.py": {
                "quarantined_at": "2024-01-15T10:30:00",
                "fail_count": 5,
                "last_error": "Connection timeout"
            }
        },
        "retry_budgets": {}
    }

    client = TestClient(app)
    resp_health = client.get("/api/gate/health")
    resp_quarantine = client.get("/api/gate/quarantine")

    assert resp_health.status_code == 200
    assert resp_health.json()["breaker_state"] == "tripped"
    assert len(resp_quarantine.json()["entries"]) == 1
    assert resp_quarantine.status_code == 200

    print("PASS")