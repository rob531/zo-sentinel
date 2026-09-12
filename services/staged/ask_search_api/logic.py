from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from app.db import get_session
from app.models import AskCorpusDoc
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

def search_ask_corpus(query: str, session: Session = Depends(get_session)) -> Dict[str, List[Dict[str, str]]]:
    """
    Search the ask corpus index for snippets matching the query using cosine similarity.
    Returns top 5 matching server snippets with relevance scores.
    """
    # Fetch all documents from the ask corpus index
    docs = session.query(AskCorpusDoc).all()

    if not docs:
        return {"servers": []}

    # Prepare documents and query for vectorization
    documents = [doc.snippet for doc in docs]
    documents.append(query)

    # Vectorize the documents and query
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(documents)

    # Compute cosine similarity between query and documents
    query_vector = tfidf_matrix[-1]
    doc_vectors = tfidf_matrix[:-1]
    cosine_similarities = cosine_similarity(query_vector, doc_vectors).flatten()

    # Pair documents with their similarity scores and sort by score descending
    scored_docs = [
        {"server_id": doc.server_id, "snippet": doc.snippet, "score": float(score)}
        for doc, score in zip(docs, cosine_similarities)
    ]
    scored_docs.sort(key=lambda x: x["score"], reverse=True)

    # Return top 5 results
    return {"servers": scored_docs[:5]}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # Setup in-memory test database
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    # Override get_session for testing
    TestingSession = Session.bind(engine)

    def override_get_session():
        return TestingSession

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    # Include the router
    from fastapi import APIRouter
    router = APIRouter(prefix="/api")
    router.get("/ask/search")(search_ask_corpus)
    app.include_router(router)

    # Test client
    client = TestClient(app)

    # Seed test data
    test_data = [
        AskCorpusDoc(server_id="server1", snippet="This is a test snippet about servers and their configurations."),
        AskCorpusDoc(server_id="server2", snippet="Another snippet discussing server performance metrics."),
        AskCorpusDoc(server_id="server3", snippet="Server security best practices and compliance standards."),
        AskCorpusDoc(server_id="server4", snippet="How to optimize server response times and reduce latency."),
        AskCorpusDoc(server_id="server5", snippet="Server maintenance schedules and backup procedures."),
    ]
    TestingSession.add_all(test_data)
    TestingSession.commit()

    # Test the endpoint
    response = client.get("/api/ask/search?q=server performance")
    assert response.status_code == 200
    data = response.json()
    assert len(data["servers"]) == 5
    assert data["servers"][0]["server_id"] == "server2"  # Known top result for this query

    print("PASS")