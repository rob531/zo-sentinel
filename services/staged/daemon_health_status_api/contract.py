from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import Base


THRESHOLDS = {
    "inference_router": 60,
    "manager_agent": 60,
    "pipeline_bridge": 60,
    "t2_consumer": 120,
    "gate_scheduler": 120,
    "self_diagnostics": 600,
    "mcp_scanner": 300,
    "signal_analyser": 60,
    "trust_synthesiser": 300,
    "threat_intel_ingestor": 300,
    "attestation_engine": 300,
    "rug_pull_monitor": 28800,
    "risk_ranker": 300,
    "world_article_feeder": 300,
    "data_velocity": 120,
    "anti_entropy": 120,
    "wisdom_synthesiser": 300,
    "gate_orchestrator": 300,
    "write_service": 300,
    "sentinel_directive_generator": 7500,
    "zo_sentinel_builder": 600,
    "build_watcher_api": 600,
}


class DaemonStatus(BaseModel):
    name: str
    age_seconds: float
    status: str
    threshold_seconds: int


class DaemonHealthResponse(BaseModel):
    services: List[DaemonStatus]


def _query_service_health_rows():
    import requests
    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": "SELECT name, last_heartbeat FROM service_health"}
    )
    resp.raise_for_status()
    return resp.json().get("rows", [])


def get_daemon_health(session: Session = Depends(get_session)) -> DaemonHealthResponse:
    try:
        rows = _query_service_health_rows()
    except Exception:
        rows = []
    
    now = datetime.now(timezone.utc)
    services = []
    for row in rows:
        name = row.get("name", "")
        last_heartbeat_str = row.get("last_heartbeat")
        if not last_heartbeat_str:
            continue
        try:
            last_heartbeat = datetime.fromisoformat(last_heartbeat_str.replace("Z", "+00:00"))
        except Exception:
            continue
        age_seconds = (now - last_heartbeat).total_seconds()
        threshold = THRESHOLDS.get(name, 300)
        status = "ok" if age_seconds < threshold else "stale"
        services.append(DaemonStatus(
            name=name,
            age_seconds=age_seconds,
            status=status,
            threshold_seconds=threshold
        ))
    return DaemonHealthResponse(services=services)


def create_app() -> FastAPI:
    app = FastAPI()
    from fastapi import APIRouter
    router = APIRouter(prefix="/api")
    router.add_api_route("/daemon-health", get_daemon_health, methods=["GET"], response_model=DaemonHealthResponse)
    app.include_router(router)
    return app


app = create_app()


if __name__ == "__main__":
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    test_client = TestClient(app)
    that_app = test_client.app
    that_app.dependency_overrides[get_session] = override_get_session
    
    import requests_mock
    with requests_mock.Mocker() as m:
        now = datetime.now(timezone.utc)
        from datetime import timedelta
        stale_time = (now - timedelta(seconds=500)).isoformat()
        ok_time = (now - timedelta(seconds=30)).isoformat()
        another_ok_time = (now - timedelta(seconds=10)).isoformat()
        
        m.post("http://127.0.0.1:8772/query", json={
            "rows": [
                {"name": "risk_ranker", "last_heartbeat": stale_time},
                {"name": "data_velocity", "last_heartbeat": ok_time},
                {"name": "signal_analyser", "last_heartbeat": another_ok_time},
            ]
        })
        
        response = test_client.get("/api/daemon-health")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    services = data.get("services", [])
    assert len(services) >= 3, f"Expected at least 3 services, got {len(services)}"
    
    stale_count = sum(1 for s in services if s["status"] == "stale")
    ok_count = sum(1 for s in services if s["status"] == "ok")
    
    assert stale_count >= 1, f"Expected at least 1 stale, got {stale_count}"
    assert ok_count >= 1, f"Expected at least 1 ok, got {ok_count}"
    
    print("PASS")