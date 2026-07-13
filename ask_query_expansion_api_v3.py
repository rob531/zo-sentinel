from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Dict
from app.db import get_session
from app.models import MCPQueryExpansionModel
import httpx
from sqlalchemy.orm import Session

router = APIRouter()

class QueryExpansionRequest(BaseModel):
    query: str

class QueryExpansionResponse(BaseModel):
    expanded_terms: List[Dict[str, float]]

@router.post("/ask/query_expansion", response_model=QueryExpansionResponse)
async def expand_query(
    request: QueryExpansionRequest,
    session: Session = Depends(get_session)
):
    # Get the pre-trained model from the database
    model = session.query(MCPQueryExpansionModel).first()
    if not model:
        raise HTTPException(status_code=404, detail="Query expansion model not found")

    # Call the ZoComputer service for query expansion
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8772/query",
            json={"query": request.query, "model_id": model.id}
        )
        response.raise_for_status()
        expanded_terms = response.json().get("expanded_terms", [])

    return {"expanded_terms": expanded_terms}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    test_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

    app.dependency_overrides[get_session] = lambda: TestSession()

    # Add test data
    with TestSession() as session:
        session.add(MCPQueryExpansionModel(id=1, name="Test Model"))
        session.commit()

    client = TestClient(app)

    # Test the endpoint
    response = client.post(
        "/ask/query_expansion",
        json={"query": "test query"}
    )
    assert response.status_code == 200
    assert "expanded_terms" in response.json()
    print("PASS")