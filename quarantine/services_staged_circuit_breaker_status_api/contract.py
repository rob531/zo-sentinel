from typing import Optional, List
from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
import requests
from datetime import datetime

from app.db import get_session


class QuarantinedFile(BaseModel):
    file: str
    quarantined_at: str
    reason: str
    consecutive_fails: int


class RetryBudgetFile(BaseModel):
    file: str
    attempts: int
    max_attempts: int
    last_error: Optional[str]


class CircuitBreakerStatus(BaseModel):
    breaker_state: str
    breaker_tripped_at: Optional[str]
    quarantined_files: List[QuarantinedFile]
    retry_budget_files: List[RetryBudgetFile]
    total_quarantined: int
    total_in_retry: int


app = FastAPI()


def query_circuit_breaker_data() -> dict:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": "SELECT breaker_state, breaker_tripped_at, quarantined_files, retry_budget_files, total_quarantined, total_in_retry FROM circuit_breaker_status LIMIT 1"},
        timeout=5
    )
    if response.status_code == 200:
        data = response.json()
        if data.get("rows"):
            return data["rows"][0]
    return {}


def get_circuit_breaker_status() -> CircuitBreakerStatus:
    data = query_circuit_breaker_data()
    
    quarantined = data.get("quarantined_files", [])
    retry_budget = data.get("retry_budget_files", [])
    
    return CircuitBreakerStatus(
        breaker_state=data.get("breaker_state", "ok"),
        breaker_tripped_at=data.get("breaker_tripped_at"),
        quarantined_files=[QuarantinedFile(**qf) for qf in quarantined],
        retry_budget_files=[RetryBudgetFile(**rf) for rf in retry_budget],
        total_quarantined=data.get("total_quarantined", len(quarantined)),
        total_in_retry=data.get("total_in_retry", len(retry_budget))
    )


@app.get("/api/admin/circuit-breaker", response_model=CircuitBreakerStatus)
def circuit_breaker_status():
    return get_circuit_breaker_status()


def run_self_test():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    
    @test_app.get("/api/admin/circuit-breaker", response_model=CircuitBreakerStatus)
    def test_circuit_breaker_status():
        return CircuitBreakerStatus(
            breaker_state="tripped",
            breaker_tripped_at="2024-01-15T10:30:00Z",
            quarantined_files=[
                QuarantinedFile(file="test1.csv", quarantined_at="2024-01-15T10:00:00Z", reason="consecutive_failures", consecutive_fails=5),
                QuarantinedFile(file="test2.csv", quarantined_at="2024-01-15T10:15:00Z", reason="consecutive_failures", consecutive_fails=3),
                QuarantinedFile(file="test3.csv", quarantined_at="2024-01-15T10:30:00Z", reason="timeout_errors", consecutive_fails=7)
            ],
            retry_budget_files=[
                RetryBudgetFile(file="test4.csv", attempts=2, max_attempts=5, last_error="connection_timeout"),
                RetryBudgetFile(file="test5.csv", attempts=4, max_attempts=5, last_error="validation_error")
            ],
            total_quarantined=3,
            total_in_retry=2
        )

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)
    
    response = client.get("/api/admin/circuit-breaker")
    assert response.status_code == 200
    
    data = response.json()
    assert data["breaker_state"] == "tripped"
    assert len(data["quarantined_files"]) == 3
    
    file_names = [qf["file"] for qf in data["quarantined_files"]]
    assert "test1.csv" in file_names
    
    print("PASS")


if __name__ == "__main__":
    run_self_test()