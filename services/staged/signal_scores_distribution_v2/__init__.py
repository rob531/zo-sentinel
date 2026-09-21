from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpScoreDispute, McpServerRegistry, McpLlmAxisScore, VulnAdvisory
from typing import List, Optional
import requests

def get_mcp_score_disputes(db: Session = Depends(get_session)) -> List[McpScoreDispute]:
    return db.query(McpScoreDispute).all()

def get_mcp_server_registries(db: Session = Depends(get_session)) -> List[McpServerRegistry]:
    return db.query(McpServerRegistry).all()

def get_mcp_llm_axis_scores(db: Session = Depends(get_session)) -> List[McpLlmAxisScore]:
    return db.query(McpLlmAxisScore).all()

def get_vuln_advisories(db: Session = Depends(get_session)) -> List[VulnAdvisory]:
    return db.query(VulnAdvisory).all()

def query_zo_computer(endpoint: str, params: Optional[dict] = None) -> dict:
    url = f"http://127.0.0.1:8772/{endpoint}"
    try:
        response = requests.post(url, json=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Error querying ZoComputer: {e}")

if __name__ == "__main__":
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Setup in-memory database for testing
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    # Override dependency for testing
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(engine)

    # Test functions
    try:
        # Test app.db functions
        db = Session(engine)
        db.query(McpScoreDispute).all()
        db.query(McpServerRegistry).all()
        db.query(McpLlmAxisScore).all()
        db.query(VulnAdvisory).all()

        # Test ZoComputer query
        query_zo_computer("query", {"sql": "SELECT 1"})

        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")
    finally:
        db.close()