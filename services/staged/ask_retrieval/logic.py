from typing import List, Optional
from fastapi import Depends
from sqlalchemy.orm import Session
from pydantic import BaseModel
from app.db import get_session
from app.models import AskCorpusIndex

class SnippetResult(BaseModel):
    server_id: int
    snippet: str
    terms: List[str]

class AskRetrievalResponse(BaseModel):
    results: List[SnippetResult]

def get_ask_retrieval_results(
    session: Session = Depends(get_session),
    limit: Optional[int] = 10
) -> AskRetrievalResponse:
    """Retrieve snippets from the ask corpus index."""
    results = session.query(AskCorpusIndex).limit(limit).all()
    return AskRetrievalResponse(
        results=[
            SnippetResult(
                server_id=result.server_id,
                snippet=result.snippet,
                terms=result.terms
            )
            for result in results
        ]
    )

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Override the session for testing
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    # Seed test data
    test_corpus = AskCorpusIndex(
        server_id=1,
        snippet="This is a test snippet",
        terms=["test", "snippet"]
    )
    test_session.add(test_corpus)
    test_session.commit()

    # Override the dependency for testing
    from app import dependency_overrides
    dependency_overrides[get_session] = lambda: test_session

    # Test the function
    response = get_ask_retrieval_results(limit=1)
    assert response.results[0].snippet == "This is a test snippet"
    print("PASS")