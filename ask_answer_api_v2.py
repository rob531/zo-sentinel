from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from app.db import get_session
from app.models import AskCorpusIndex
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

router = APIRouter()

class QuestionPayload(BaseModel):
    question: str
    server_id: int

class AnswerSnippet(BaseModel):
    snippet: str
    relevance_score: float

class AnswerResponse(BaseModel):
    server_id: int
    question: str
    snippets: List[AnswerSnippet]

@router.post("/ask/answer", response_model=AnswerResponse)
async def get_answers(
    payload: QuestionPayload,
    db: Session = Depends(get_session)
) -> AnswerResponse:
    try:
        results = db.query(AskCorpusIndex).filter(
            AskCorpusIndex.server_id == payload.server_id
        ).all()

        if not results:
            raise HTTPException(status_code=404, detail="No matching snippets found")

        snippets = [
            AnswerSnippet(
                snippet=result.snippet,
                relevance_score=result.relevance_score
            ) for result in results
        ]

        return AnswerResponse(
            server_id=payload.server_id,
            question=payload.question,
            snippets=snippets
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    from fastapi import FastAPI
    from app.db import Base, engine
    from sqlalchemy.orm import sessionmaker

    # Override the dependency for testing
    app = FastAPI()
    app.include_router(router)

    # Create a test database
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed test data
    test_db = SessionLocal()
    test_db.add(AskCorpusIndex(
        server_id=1,
        snippet="This is a test snippet for server 1",
        relevance_score=0.95
    ))
    test_db.commit()

    # Override the dependency for the test
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Test the endpoint
    client = TestClient(app)
    response = client.post(
        "/ask/answer",
        json={"question": "test question", "server_id": 1}
    )

    assert response.status_code == 200
    assert len(response.json()["snippets"]) > 0
    print("PASS")