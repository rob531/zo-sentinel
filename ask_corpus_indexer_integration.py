from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores, MCPScoreDisputes, Orgs, Users
from typing import List, Optional
import requests
import json

app = FastAPI()

def get_indexed_corpus(session: Session = Depends(get_session)):
    """Retrieve the indexed corpus from the database."""
    try:
        corpus = session.query(MCPServerRegistry).all()
        return corpus
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def update_index(corpus: List[MCPServerRegistry], session: Session = Depends(get_session)):
    """Update the index with the current corpus."""
    try:
        for item in corpus:
            # Prepare data for indexing
            data = {
                "id": item.id,
                "content": item.content,
                "metadata": {
                    "created_at": str(item.created_at),
                    "updated_at": str(item.updated_at),
                    "org_id": item.org_id,
                    "user_id": item.user_id
                }
            }
            # Send data to the indexer
            response = requests.post("http://127.0.0.1:8772/index", json=data)
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Failed to index item {item.id}")
        return {"status": "success", "message": "Index updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def search_corpus(query: str, session: Session = Depends(get_session)):
    """Search the indexed corpus."""
    try:
        # Send search query to the indexer
        response = requests.post("http://127.0.0.1:8772/search", json={"query": query})
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to search corpus")
        return response.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/corpus/index")
async def index_corpus(session: Session = Depends(get_session)):
    """Index the corpus."""
    corpus = get_indexed_corpus(session)
    return update_index(corpus, session)

@app.get("/corpus/search")
async def search(query: str, session: Session = Depends(get_session)):
    """Search the corpus."""
    return search_corpus(query, session)

if __name__ == "__main__":
    import tempfile
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test data
    test_data = [
        MCPServerRegistry(
            id=1,
            content="Test content 1",
            org_id=1,
            user_id=1,
            created_at="2023-01-01",
            updated_at="2023-01-01"
        ),
        MCPServerRegistry(
            id=2,
            content="Test content 2",
            org_id=1,
            user_id=1,
            created_at="2023-01-02",
            updated_at="2023-01-02"
        )
    ]

    # Add test data to the session
    session = SessionLocal()
    session.add_all(test_data)
    session.commit()

    # Test indexing
    index_response = index_corpus(session)
    if index_response["status"] != "success":
        print("FAIL")
        exit(1)

    # Test searching
    search_response = search_corpus("Test content 1", session)
    if len(search_response["results"]) == 0:
        print("FAIL")
        exit(1)

    print("PASS")