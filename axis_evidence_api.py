from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPLLMAxisScores

router = APIRouter()

class EvidenceResponse(BaseModel):
    axis_name: str
    evidence_blob: str
    scored_at: str

@router.get("/axis/evidence/{server_id}", response_model=list[EvidenceResponse])
async def get_axis_evidence(server_id: int, session: Session = Depends(get_session)):
    evidence = session.query(
        MCPLLMAxisScores.axis_name,
        MCPLLMAxisScores.evidence_blob,
        MCPLLMAxisScores.scored_at
    ).filter(
        MCPLLMAxisScores.server_id == server_id
    ).all()

    if not evidence:
        raise HTTPException(status_code=404, detail="No evidence found for the given server_id")

    return [EvidenceResponse(**dict(row._mapping)) for row in evidence]

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPLLMAxisScores
    from app.main import app

    # Override the session for testing
    test_engine = engine
    Base.metadata.create_all(test_engine)
    test_session = get_session(override=True)

    # Seed test data
    test_data = [
        MCPLLMAxisScores(
            server_id=1,
            axis_name="security",
            evidence_blob="Test evidence for security axis",
            scored_at="2023-01-01T00:00:00"
        ),
        MCPLLMAxisScores(
            server_id=1,
            axis_name="performance",
            evidence_blob="Test evidence for performance axis",
            scored_at="2023-01-01T00:00:00"
        )
    ]
    test_session.add_all(test_data)
    test_session.commit()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/axis/evidence/1")
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert response.json()[0]["axis_name"] == "security"
    assert response.json()[1]["axis_name"] == "performance"

    print("PASS")