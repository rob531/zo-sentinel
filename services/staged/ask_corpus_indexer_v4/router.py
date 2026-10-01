from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import AskCorpusDoc
from pydantic import BaseModel

router = APIRouter()

class AskCorpusIndexResponse(BaseModel):
    server_id: str
    snippet: str
    terms: str
    content_hash: str

@router.get("/api/ask/corpus/indexer", response_model=AskCorpusIndexResponse)
def get_ask_corpus_indexer(server_id: str, session: Session = Depends(get_session)):
    ask_corpus_doc = session.query(AskCorpusDoc).filter(AskCorpusDoc.server_id == server_id).first()
    if ask_corpus_doc:
        return AskCorpusIndexResponse(
            server_id=ask_corpus_doc.server_id,
            snippet=ask_corpus_doc.snippet,
            terms=ask_corpus_doc.terms,
            content_hash=ask_corpus_doc.content_hash
        )
    return None

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    from fastapi import FastAPI
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)

    test_server_id = "test_server"
    test_snippet = "test snippet"
    test_terms = "test terms"
    test_content_hash = "test content hash"

    db = TestingSessionLocal()
    test_doc = AskCorpusDoc(
        server_id=test_server_id,
        snippet=test_snippet,
        terms=test_terms,
        content_hash=test_content_hash
    )
    db.add(test_doc)
    db.commit()
    db.close()

    response = client.get(f"/api/ask/corpus/indexer?server_id={test_server_id}")
    assert response.status_code == 200
    assert response.json() == {
        "server_id": test_server_id,
        "snippet": test_snippet,
        "terms": test_terms,
        "content_hash": test_content_hash
    }
    print("PASS")