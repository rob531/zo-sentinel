from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Org, User
from typing import List, Optional
import requests
from datetime import datetime

app = FastAPI()

def get_server_export_api_status(session: Session = Depends(get_session)) -> Optional[str]:
    """Check the status of server_export_api.py by querying the latest Gate-8 results."""
    latest_record = session.query(MCPServerRegistry).order_by(MCPServerRegistry.timestamp.desc()).first()
    if not latest_record:
        return None
    return latest_record.gate_8_status

def get_recent_gate_8_fails(session: Session = Depends(get_session)) -> List[dict]:
    """Get the last 4 Gate-8 fails for server_export_api.py."""
    fails = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.gate_8_status == 'fail'
    ).order_by(MCPServerRegistry.timestamp.desc()).limit(4).all()
    return [{'timestamp': fail.timestamp, 'status': fail.gate_8_status} for fail in fails]

def get_smoke_test_failure_details(session: Session = Depends(get_session)) -> Optional[dict]:
    """Get details of the smoke-test failure from MCPScoreDisputes."""
    latest_dispute = session.query(MCPScoreDisputes).filter(
        MCPScoreDisputes.server_export_api_id.isnot(None)
    ).order_by(MCPScoreDisputes.timestamp.desc()).first()
    if not latest_dispute:
        return None
    return {
        'timestamp': latest_dispute.timestamp,
        'failure_reason': latest_dispute.failure_reason,
        'resolved': latest_dispute.resolved
    }

def query_mesh_memory(server_id: int) -> Optional[dict]:
    """Query the ZoComputer store for mesh_memory related to the server_export_api.py."""
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT * FROM mesh_memory WHERE server_id = :server_id",
                "params": {"server_id": server_id}
            }
        )
        response.raise_for_status()
        return response.json().get('data', [{}])[0]
    except requests.RequestException:
        return None

@app.get("/investigate_server_export_api")
async def investigate_server_export_api(session: Session = Depends(get_session)):
    """Investigate the server_export_api.py breaker action."""
    status = get_server_export_api_status(session)
    if status != 'fail':
        return {"status": "success", "message": "server_export_api.py is not in a failed state"}

    recent_fails = get_recent_gate_8_fails(session)
    if len(recent_fails) < 4:
        return {"status": "success", "message": "Less than 4 consecutive Gate-8 fails"}

    smoke_test_details = get_smoke_test_failure_details(session)
    if not smoke_test_details:
        return {"status": "success", "message": "No smoke-test failure details found"}

    server_id = recent_fails[0]['timestamp'].timestamp()
    mesh_memory = query_mesh_memory(server_id)

    return {
        "status": "investigating",
        "message": "Investigating server_export_api.py breaker action",
        "recent_fails": recent_fails,
        "smoke_test_details": smoke_test_details,
        "mesh_memory": mesh_memory
    }

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for self-test
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Mock data for self-test
    test_session = TestSession()
    test_session.add(MCPServerRegistry(
        timestamp=datetime.now(),
        gate_8_status='fail'
    ))
    test_session.add(MCPServerRegistry(
        timestamp=datetime.now(),
        gate_8_status='fail'
    ))
    test_session.add(MCPServerRegistry(
        timestamp=datetime.now(),
        gate_8_status='fail'
    ))
    test_session.add(MCPServerRegistry(
        timestamp=datetime.now(),
        gate_8_status='fail'
    ))
    test_session.add(MCPScoreDisputes(
        server_export_api_id=1,
        timestamp=datetime.now(),
        failure_reason="Smoke test failed",
        resolved=False
    ))
    test_session.commit()

    # Run self-test
    import uvicorn
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/investigate_server_export_api")
    assert response.status_code == 200
    assert response.json()["status"] == "investigating"
    print("PASS")