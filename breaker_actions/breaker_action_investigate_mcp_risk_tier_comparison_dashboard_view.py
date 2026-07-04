from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Org, User
import requests
from typing import List, Dict, Optional
import logging

app = FastAPI()
logger = logging.getLogger(__name__)

class MCPServerInfo:
    def __init__(self, server_id: int, org_id: int, org_name: str, llm_scores: List[Dict], disputes: List[Dict]):
        self.server_id = server_id
        self.org_id = org_id
        self.org_name = org_name
        self.llm_scores = llm_scores
        self.disputes = disputes

def get_mcp_server_info(server_id: int, session: Session = Depends(get_session)) -> Optional[MCPServerInfo]:
    server = session.query(MCPServerRegistry).filter(MCPServerRegistry.id == server_id).first()
    if not server:
        return None

    org = session.query(Org).filter(Org.id == server.org_id).first()
    if not org:
        return None

    llm_scores = session.query(MCPLLMAxisScores).filter(MCPLLMAxisScores.server_id == server_id).all()
    disputes = session.query(MCPScoreDisputes).filter(MCPScoreDisputes.server_id == server_id).all()

    return MCPServerInfo(
        server_id=server.id,
        org_id=server.org_id,
        org_name=org.name,
        llm_scores=[{"axis": score.axis, "value": score.value} for score in llm_scores],
        disputes=[{"id": dispute.id, "reason": dispute.reason} for dispute in disputes]
    )

def get_signal_scores(server_id: int) -> Optional[List[Dict]]:
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": f"SELECT * FROM mcp_signal_scores WHERE server_id = {server_id}"}
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        logger.error(f"Error fetching signal scores: {e}")
        return None

@app.get("/investigate/{server_id}")
async def investigate(server_id: int, session: Session = Depends(get_session)):
    server_info = get_mcp_server_info(server_id, session)
    if not server_info:
        raise HTTPException(status_code=404, detail="Server not found")

    signal_scores = get_signal_scores(server_id)

    investigation_data = {
        "server_id": server_info.server_id,
        "org_id": server_info.org_id,
        "org_name": server_info.org_name,
        "llm_scores": server_info.llm_scores,
        "disputes": server_info.disputes,
        "signal_scores": signal_scores
    }

    return investigation_data

if __name__ == "__main__":
    import tempfile
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for self-test
    db_file = tempfile.mktemp()
    engine = create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    # Override dependency for testing
    app.dependency_overrides[get_session] = lambda: TestSession()

    # Test data setup
    test_session = TestSession()
    test_org = Org(id=1, name="Test Org")
    test_server = MCPServerRegistry(id=1, org_id=1)
    test_llm_score = MCPLLMAxisScores(server_id=1, axis="test_axis", value=0.8)
    test_dispute = MCPScoreDisputes(server_id=1, reason="Test dispute")

    test_session.add_all([test_org, test_server, test_llm_score, test_dispute])
    test_session.commit()

    # Run self-test
    try:
        response = app.client.get("/investigate/1")
        assert response.status_code == 200
        data = response.json()
        assert data["server_id"] == 1
        assert data["org_name"] == "Test Org"
        assert len(data["llm_scores"]) == 1
        assert len(data["disputes"]) == 1
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        test_session.close()
        import os
        os.remove(db_file)