# services/staged/factory_liveness_continuity_probe/contract.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, DateTime, Integer
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base as AppBase

class ServiceHealthRecord(BaseModel):
    service: str
    status: str
    last_heartbeat: datetime

class CadenceJobRunRecord(BaseModel):
    job: str
    status: str
    started_at: datetime
    finished_at: datetime | None

class ProbeResult(BaseModel):
    service: str
    status: str
    last_heartbeat_iso: str
    sla_threshold_s: int
    breached: bool
    breach_seconds: int | None
    job_status: str | None

class ContinuityProbesResponse(BaseModel):
    probes: list[ProbeResult]

SLA_THRESHOLDS = {
    "inference_router": 120,
    "signal_analyser": 60,
    "trust_synthesiser": 600,
    "mcp_scanner": 300,
    "threat_intel_ingestor": 900,
}

def get_sla_threshold(service: str) -> int:
    return SLA_THRESHOLDS.get(service, 300)

def compute_continuity_probes(
    session: Session,
) -> ContinuityProbesResponse:
    now = datetime.now(timezone.utc)

    service_health_data = []
    cadence_job_runs_data = []

    try:
        import requests
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "type": "service_health",
                "params": {}
            },
            timeout=5
        )
        if resp.status_code == 200:
            service_health_data = resp.json().get("results", [])
    except Exception:
        pass

    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "type": "cadence_job_runs",
                "params": {}
            },
            timeout=5
        )
        if resp.status_code == 200:
            cadence_job_runs_data = resp.json().get("results", [])
    except Exception:
        pass

    service_map: dict[str, dict] = {}
    for record in service_health_data:
        service_name = record.get("service", "")
        last_hb = record.get("last_heartbeat")
        if isinstance(last_hb, str):
            last_hb = datetime.fromisoformat(last_hb.replace("Z", "+00:00"))
        service_map[service_name] = {
            "status": record.get("status", "unknown"),
            "last_heartbeat": last_hb,
        }

    job_status_map: dict[str, str] = {}
    for record in cadence_job_runs_data:
        job_name = record.get("job", "")
        status = record.get("status", "")
        started_at = record.get("started_at")
        finished_at = record.get("finished_at")

        if isinstance(started_at, str):
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if isinstance(finished_at, str):
            finished_at = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))

        if status == "running" and finished_at is None and started_at is not None:
            age_s = (now - started_at).total_seconds()
            if age_s > 600:
                job_status_map[job_name] = "stale_running"

    probes: list[ProbeResult] = []
    for service_name, data in service_map.items():
        last_hb = data["last_heartbeat"]
        threshold = get_sla_threshold(service_name)

        if last_hb.tzinfo is None:
            last_hb = last_hb.replace(tzinfo=timezone.utc)

        age_s = (now - last_hb).total_seconds()
        breached = age_s > threshold
        breach_seconds = int(age_s - threshold) if breached else None

        job_status = job_status_map.get(service_name)

        probes.append(ProbeResult(
            service=service_name,
            status=data["status"],
            last_heartbeat_iso=last_hb.isoformat(),
            sla_threshold_s=threshold,
            breached=breached,
            breach_seconds=breach_seconds,
            job_status=job_status,
        ))

    return ContinuityProbesResponse(probes=probes)

def create_router() -> FastAPI:
    app = FastAPI()

    @app.get("/api/factory/liveness/continuity", response_model=ContinuityProbesResponse)
    def get_continuity_probes(db: Session = Depends(get_session)):
        return compute_continuity_probes(db)

    return app

def run_self_test():
    from sqlalchemy.pool import StaticPool

    mock_data: dict[str, Any] = {
        "service_health": [
            {"service": "healthy_router", "status": "healthy", "last_heartbeat": (datetime.now(timezone.utc) - timedelta(seconds=50)).isoformat()},
            {"service": "healthy_analyser", "status": "healthy", "last_heartbeat": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()},
            {"service": "inference_router", "status": "stale", "last_heartbeat": (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()},
            {"service": "mcp_scanner", "status": "dead", "last_heartbeat": (datetime.now(timezone.utc) - timedelta(seconds=500)).isoformat()},
        ],
        "cadence_job_runs": [],
    }

    def mock_post(url, json=None, timeout=None):
        resp = MagicMock()
        query_type = json.get("type") if json else None
        resp.status_code = 200
        if query_type == "service_health":
            resp.json.return_value = {"results": mock_data["service_health"]}
        elif query_type == "cadence_job_runs":
            resp.json.return_value = {"results": mock_data["cadence_job_runs"]}
        else:
            resp.json.return_value = {"results": []}
        return resp

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AppBase.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = create_router()
    app.dependency_overrides[get_session] = override_get_session

    with patch("requests.post", mock_post):
        client = TestClient(app)
        response = client.get("/api/factory/liveness/continuity")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    probes = data.get("probes", [])

    breached_services = [p for p in probes if p.get("breached")]
    assert len(breached_services) == 2, f"Expected exactly 2 breached services, got {len(breached_services)}: {[p['service'] for p in breached_services]}"

    breached_names = {p["service"] for p in breached_services}
    assert "inference_router" in breached_names, "inference_router should be breached"
    assert "mcp_scanner" in breached_names, "mcp_scanner should be breached"

    print("PASS")

if __name__ == "__main__":
    run_self_test()