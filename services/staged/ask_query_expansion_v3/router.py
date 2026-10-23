from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from elasticsearch import Elasticsearch
from pydantic import BaseModel
from typing import List, Dict
from app.db import get_session
from app.models import AskCorpusIndex

router = APIRouter(prefix="/api")

class ExpansionTerm(BaseModel):
    term: str
    weight: float

class QueryExpansionResponse(BaseModel):
    query: str
    expansion: List[ExpansionTerm]

@router.get("/ask/query/expand", response_model=QueryExpansionResponse)
def expand_query(query: str, session: Session = Depends(get_session)):
    es = Elasticsearch()
    index_name = "ask_corpus_index"
    if not es.indices.exists(index=index_name):
        raise HTTPException(status_code=404, detail="Index not found")

    response = es.search(
        index=index_name,
        body={
            "query": {
                "match": {
                    "content": query
                }
            },
            "aggs": {
                "expansion_terms": {
                    "terms": {
                        "field": "content",
                        "size": 10
                    }
                }
            }
        }
    )

    expansion_terms = []
    for bucket in response['aggregations']['expansion_terms']['buckets']:
        expansion_terms.append(ExpansionTerm(term=bucket['key'], weight=bucket['doc_count']))

    return QueryExpansionResponse(query=query, expansion=expansion_terms)

if __name__ == "__main__":
    import pytest
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Setup in-memory SQLite database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables and seed data
    AskCorpusIndex.__table__.create(bind=engine)
    db = TestingSessionLocal()
    db.add(AskCorpusIndex(content="known term 1"))
    db.add(AskCorpusIndex(content="known term 2"))
    db.add(AskCorpusIndex(content="known term 3"))
    db.commit()

    # Override the get_session dependency
    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create test client
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/ask/query/expand?query=known term")
    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "known term"
    assert any(term["term"] == "known term 1" for term in data["expansion"])

    print("PASS")