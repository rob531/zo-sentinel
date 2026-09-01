from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import AskCorpusIndex
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/ask")

class SnippetResult(BaseModel):
    server_id: str
    snippet: str
    terms: List[str]

class AskRetrievalResponse(BaseModel):
    results: List[SnippetResult]

def get_ask_corpus_index(db: Session = Depends(get_session)):
    return db.query(AskCorpusIndex).all()

@router.get("/retrieve", response_model=AskRetrievalResponse)
async def retrieve_ask_snippets(db: Session = Depends(get_session)):
    corpus_entries = get_ask_corpus_index(db)
    results = []
    for entry in corpus_entries:
        results.append({
            "server_id": entry.server_id,
            "snippet": entry.snippet,
            "terms": entry.terms.split(",") if entry.terms else []
        })
    return {"results": results}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Override the database session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: TestingSession()

    # Seed test data
    with TestingSession() as session:
        test_entry = AskCorpusIndex(
            server_id="test-server-1",
            snippet="This is a test snippet for the ask retrieval service.",
            terms="test,snippet,ask"
        )
        session.add(test_entry)
        session.commit()

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/api/ask/retrieve")
    assert response.status_code == 200
    assert len(response.json()["results"]) == 1
    assert "test snippet" in response.json()["results"][0]["snippet"]

    print("PASS")