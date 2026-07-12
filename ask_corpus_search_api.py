from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import AskCorpusIndex

router = APIRouter()

class SearchResult(BaseModel):
    server_id: str
    snippet: str
    terms: str
    content_hash: str
    indexed_at: str

class SearchResponse(BaseModel):
    results: List[SearchResult]

def query_write_service(query: str) -> List[dict]:
    response = requests.post(
        "http://127.0.0.1:8772/query",
        json={"query": query}
    )
    response.raise_for_status()
    return response.json()

@router.get("/ask/search", response_model=SearchResponse)
async def search_corpus(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(10, ge=1, le=100),
    server_id: Optional[str] = None,
    session: get_session = Depends(get_session)
):
    query_terms = q.split()
    if not query_terms:
        raise HTTPException(status_code=400, detail="Query must contain at least one term")

    base_query = session.query(AskCorpusIndex).filter(
        AskCorpusIndex.terms.contains(q)
    )

    if server_id:
        base_query = base_query.filter(AskCorpusIndex.server_id == server_id)

    results = base_query.order_by(
        AskCorpusIndex.terms.length().desc()
    ).limit(limit).all()

    return {
        "results": [
            {
                "server_id": result.server_id,
                "snippet": result.snippet,
                "terms": result.terms,
                "content_hash": result.content_hash,
                "indexed_at": str(result.indexed_at)
            }
            for result in results
        ]
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, get_session
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    with TestSession() as session:
        session.add_all([
            AskCorpusIndex(
                server_id="server1",
                snippet="Python is a great language",
                terms="python language programming",
                content_hash="hash1",
                indexed_at="2023-01-01"
            ),
            AskCorpusIndex(
                server_id="server2",
                snippet="Java is also popular",
                terms="java programming",
                content_hash="hash2",
                indexed_at="2023-01-02"
            ),
            AskCorpusIndex(
                server_id="server3",
                snippet="Python and Java are both good",
                terms="python java programming",
                content_hash="hash3",
                indexed_at="2023-01-03"
            )
        ])
        session.commit()

    response = client.get("/ask/search?q=python")
    assert response.status_code == 200
    assert len(response.json()["results"]) >= 1
    assert any(result["server_id"] == "server1" for result in response.json()["results"])
    assert any(result["server_id"] == "server3" for result in response.json()["results"])

    print("PASS")