from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from pydantic import BaseModel
from app.db.session import get_db

router = APIRouter()

class ServerID(BaseModel):
    server_id: str

@router.get("/escalated_servers", response_model=List[ServerID])
def get_escalated_servers(db: Session = Depends(get_db)):
    query = """
    SELECT DISTINCT server_id
    FROM mcp_llm_axis_scores
    WHERE escalated = TRUE
    """
    result = db.execute(query)
    escalated_servers = [{"server_id": row[0]} for row in result.fetchall()]
    return escalated_servers

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db.session import SessionLocal
    from app.db.base import Base
    from app.db.models import MCPLLMAxisScores

    # Create in-memory database and seed with test data
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal.override(engine)

    # Test case 1: No escalated servers
    db = SessionLocal()
    db.execute("DELETE FROM mcp_llm_axis_scores")
    db.commit()

    client = TestClient(router)
    response = client.get("/escalated_servers")
    assert response.json() == []
    print("PASS: Empty list returned when no escalated servers")

    # Test case 2: With escalated servers
    db.execute("""
    INSERT INTO mcp_llm_axis_scores (server_id, escalated)
    VALUES ('server1', TRUE), ('server2', FALSE), ('server3', TRUE)
    """)
    db.commit()

    response = client.get("/escalated_servers")
    assert len(response.json()) == 2
    assert {"server_id": "server1"} in response.json()
    assert {"server_id": "server3"} in response.json()
    print("PASS: Non-empty list returned with escalated servers")