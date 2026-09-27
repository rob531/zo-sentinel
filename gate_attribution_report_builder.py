import csv
from datetime import datetime
from typing import List, Dict
from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
import requests
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, AuditLog

router = APIRouter()

def query_write_service(query: str, params: Dict = None) -> List[Dict]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query, "params": params or {}}
    )
    response.raise_for_status()
    return response.json()

def generate_gate_attribution_csv(server_id: str) -> str:
    session = Depends(get_session)()

    # Query MCP server registry
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # Query MCP LLM axis scores
    axis_scores = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id,
        MCPLLMAxisScores.axis_name == 'overall_risk'
    ).all()

    # Query audit log for gate attribution
    audit_logs = session.query(AuditLog).filter(
        AuditLog.target_server_id == server_id,
        AuditLog.timestamp >= datetime.utcnow() - timedelta(days=30)
    ).all()

    # Prepare data
    data = []
    for score in axis_scores:
        for log in audit_logs:
            data.append({
                'server_id': server_id,
                'gate_name': log.gate_name,
                'attribution_score': score.score,
                'attribution_reason': log.reason,
                'collected_at': score.created_at.isoformat()
            })

    # Write CSV
    csv_path = f"/tmp/gate_attribution_{server_id}_{datetime.utcnow().isoformat()}.csv"
    with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['server_id', 'gate_name', 'attribution_score', 'attribution_reason', 'collected_at']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(data)

    return csv_path

@router.get("/export/gate-attribution/{server_id}", response_class=Response, media_type="text/csv")
async def export_gate_attribution(server_id: str):
    csv_path = generate_gate_attribution_csv(server_id)
    with open(csv_path, 'r', encoding='utf-8') as csvfile:
        csv_content = csvfile.read()
    return Response(content=csv_content, media_type="text/csv")

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import get_session
    from app.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup test database
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    # Override dependencies for testing
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Create test data
    test_session = TestSession()
    test_server = MCPServerRegistry(server_id="test_server_1")
    test_session.add(test_server)
    test_axis_score = MCPLLMAxisScores(server_id="test_server_1", axis_name="overall_risk", score=0.8)
    test_session.add(test_axis_score)
    test_audit_log = AuditLog(target_server_id="test_server_1", gate_name="test_gate", reason="test_reason")
    test_session.add(test_audit_log)
    test_session.commit()

    # Test endpoint
    client = TestClient(app)
    response = client.get("/export/gate-attribution/test_server_1")
    assert response.status_code == 200
    assert "server_id" in response.text
    assert "test_server_1" in response.text

    print("PASS")