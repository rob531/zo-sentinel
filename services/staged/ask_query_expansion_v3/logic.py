from fastapi import Depends, HTTPException
from pydantic import BaseModel
from elasticsearch import Elasticsearch
from app.db import get_session
from app.models import AskCorpusIndex
from sqlalchemy.orm import Session

class ExpansionTerm(BaseModel):
    term: str
    weight: float

class QueryExpansionResponse(BaseModel):
    query: str
    expansion: list[ExpansionTerm]

def get_query_expansion(query: str, session: Session = Depends(get_session)) -> QueryExpansionResponse:
    es = Elasticsearch()

    # Fetch the ask_corpus_index from the database
    ask_corpus_index = session.query(AskCorpusIndex).first()
    if not ask_corpus_index:
        raise HTTPException(status_code=404, detail="Ask corpus index not found")

    # Use Elasticsearch to expand the query
    response = es.search(
        index=ask_corpus_index.index_name,
        body={
            "query": {
                "match": {
                    "content": query
                }
            },
            "aggs": {
                "expanded_terms": {
                    "significant_terms": {
                        "field": "content",
                        "exclude": [query]
                    }
                }
            }
        }
    )

    # Extract the expanded terms and their weights
    expanded_terms = response['aggregations']['expanded_terms']['buckets']
    expansion = [ExpansionTerm(term=term['key'], weight=term['score']) for term in expanded_terms]

    return QueryExpansionResponse(query=query, expansion=expansion)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the get_session dependency for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    # Create the tables and seed the database
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    db.add(AskCorpusIndex(index_name="test_index"))
    db.commit()
    db.close()

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/ask/query/expand?query=test")

    assert response.status_code == 200
    assert "test" in response.json()["query"]
    assert any(term["term"] == "known_term" for term in response.json()["expansion"])

    print("PASS")