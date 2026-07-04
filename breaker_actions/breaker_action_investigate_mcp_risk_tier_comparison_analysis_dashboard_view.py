from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
from typing import List, Dict, Any
import requests
import json
from datetime import datetime

app = FastAPI()

def get_quarantined_servers(session: Session) -> List[Dict[str, Any]]:
    """Retrieve servers that are quarantined and have failed three consecutive times."""
    servers = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.status == 'quarantined',
        MCPServerRegistry.consecutive_failures >= 3
    ).all()
    return [{
        'id': server.id,
        'name': server.name,
        'status': server.status,
        'consecutive_failures': server.consecutive_failures,
        'last_failure': server.last_failure
    } for server in servers]

def get_risk_tier_comparison_data(server_id: int, session: Session) -> Dict[str, Any]:
    """Retrieve risk tier comparison data for a specific server."""
    scores = session.query(MCPLLMAxisScores).filter(
        MCPLLMAxisScores.server_id == server_id
    ).all()
    disputes = session.query(MCPScoreDisputes).filter(
        MCPScoreDisputes.server_id == server_id
    ).all()

    return {
        'scores': [{
            'id': score.id,
            'axis': score.axis,
            'score': score.score,
            'timestamp': score.timestamp
        } for score in scores],
        'disputes': [{
            'id': dispute.id,
            'score_id': dispute.score_id,
            'reason': dispute.reason,
            'timestamp': dispute.timestamp
        } for dispute in disputes]
    }

def get_org_and_user_data(server_id: int, session: Session) -> Dict[str, Any]:
    """Retrieve organization and user data associated with a specific server."""
    server = session.query(MCPServerRegistry).filter(
        MCPServerRegistry.id == server_id
    ).first()

    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    org = session.query(Orgs).filter(
        Orgs.id == server.org_id
    ).first()

    user = session.query(Users).filter(
        Users.id == server.user_id
    ).first()

    return {
        'org': {
            'id': org.id,
            'name': org.name,
            'description': org.description
        } if org else None,
        'user': {
            'id': user.id,
            'name': user.name,
            'email': user.email
        } if user else None
    }

def query_zo_computer(endpoint: str, query: Dict[str, Any]) -> Dict[str, Any]:
    """Query the ZoComputer store for mesh/pipeline data."""
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json=query
    )
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=response.text)
    return response.json()

def get_mesh_memory_data(server_id: int) -> Dict[str, Any]:
    """Retrieve mesh memory data for a specific server."""
    query = {
        "table": "mesh_memory",
        "filter": {
            "server_id": server_id
        }
    }
    return query_zo_computer("mesh_memory", query)

def get_signal_scores_data(server_id: int) -> Dict[str, Any]:
    """Retrieve signal scores data for a specific server."""
    query = {
        "table": "mcp_signal_scores",
        "filter": {
            "server_id": server_id
        }
    }
    return query_zo_computer("mcp_signal_scores", query)

@app.get("/investigate/{server_id}", response_model=Dict[str, Any])
async def investigate(server_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    """Investigate the root cause of a quarantined server."""
    server_data = get_quarantined_servers(session)
    if not any(server['id'] == server_id for server in server_data):
        raise HTTPException(status_code=404, detail="Server not found or not quarantined")

    risk_tier_data = get_risk_tier_comparison_data(server_id, session)
    org_user_data = get_org_and_user_data(server_id, session)
    mesh_memory_data = get_mesh_memory_data(server_id)
    signal_scores_data = get_signal_scores_data(server_id)

    return {
        'server': next(server for server in server_data if server['id'] == server_id),
        'risk_tier_data': risk_tier_data,
        'org_user_data': org_user_data,
        'mesh_memory_data': mesh_memory_data,
        'signal_scores_data': signal_scores_data,
        'investigation_timestamp': datetime.utcnow().isoformat()
    }

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the dependency for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Mock data for self-test
    from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users

    session = SessionLocal()
    session.add(MCPServerRegistry(
        id=1,
        name="Test Server",
        status="quarantined",
        consecutive_failures=3,
        last_failure=datetime.utcnow(),
        org_id=1,
        user_id=1
    ))
    session.add(MCPLLMAxisScores(
        id=1,
        server_id=1,
        axis="risk",
        score=0.8,
        timestamp=datetime.utcnow()
    ))
    session.add(MCPScoreDisputes(
        id=1,
        server_id=1,
        score_id=1,
        reason="Disputed score",
        timestamp=datetime.utcnow()
    ))
    session.add(Orgs(
        id=1,
        name="Test Org",
        description="Test Org Description"
    ))
    session.add(Users(
        id=1,
        name="Test User",
        email="test@example.com"
    ))
    session.commit()

    # Self-test
    import uvicorn
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/investigate/1")
    assert response.status_code == 200
    print("PASS")