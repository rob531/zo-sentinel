from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from typing import Optional

class QueryExpansionRequest(BaseModel):
    query: str

class QueryExpansionResponse(BaseModel):
    expanded_query: str

def get_contract():
    return {
        "name": "ask_query_expansion_v2",
        "description": "Expands user queries with synonyms and related terms",
        "endpoint": "/api/ask/query_expansion",
        "method": "POST",
        "request_body": QueryExpansionRequest,
        "response_body": QueryExpansionResponse,
    }

def list_contracts():
    return [get_contract()]

def logic(query: str) -> str:
    # Simple expansion logic for demonstration
    synonyms = {
        "vulnerability": ["vuln", "flaw", "weakness"],
        "exploit": ["attack", "hack"],
        "threat": ["risk", "danger"],
    }

    expanded_terms = []
    for term in query.split():
        if term.lower() in synonyms:
            expanded_terms.extend(synonyms[term.lower()])

    return f"{query} {' '.join(expanded_terms)}"

def router(request: QueryExpansionRequest, db: Session = Depends(get_session)) -> QueryExpansionResponse:
    expanded_query = logic(request.query)
    return QueryExpansionResponse(expanded_query=expanded_query)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Setup in-memory SQLite for self-test
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Mock dependency override
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Create test app
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = get_test_session

    # Add test route
    @test_app.post("/api/ask/query_expansion")
    async def test_router(request: QueryExpansionRequest, db: Session = Depends(get_session)):
        return router(request, db)

    # Run self-test
    from fastapi.testclient import TestClient
    client = TestClient(test_app)

    test_query = "vulnerability exploit"
    response = client.post(
        "/api/ask/query_expansion",
        json={"query": test_query}
    )

    assert response.status_code == 200
    assert test_query in response.json()["expanded_query"]
    print("PASS")