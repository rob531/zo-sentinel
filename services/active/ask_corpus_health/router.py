from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import AskCorpusDoc
import json

router = APIRouter(prefix="/api/ask/corpus", tags=["corpus"])


class CorpusHealthResponse(BaseModel):
    size: int
    last_indexed_at: str | None
    term_distribution: dict[str, int]
    hash_distribution: dict[str, int]


def health(session: Session) -> CorpusHealthResponse:
    docs = session.query(AskCorpusDoc).all()

    size = len(docs)

    last_indexed_at = None
    if docs:
        max_dt = max(d.indexed_at for d in docs if d.indexed_at)
        if max_dt:
            last_indexed_at = max_dt.isoformat()

    term_counts: dict[str, int] = {}
    hash_counts: dict[str, int] = {}

    for doc in docs:
        if doc.content_hash:
            hash_key = doc.content_hash[:8]
            hash_counts[hash_key] = hash_counts.get(hash_key, 0) + 1

        if doc.terms:
            try:
                terms_list = json.loads(doc.terms) if doc.terms.startswith("[") else doc.terms.split(",")
            except (json.JSONDecodeError, AttributeError):
                terms_list = doc.terms.split()
            for t in terms_list:
                t = t.strip()
                if t:
                    term_counts[t] = term_counts.get(t, 0) + 1

    return CorpusHealthResponse(
        size=size,
        last_indexed_at=last_indexed_at,
        term_distribution=term_counts,
        hash_distribution=hash_counts,
    )


@router.get("/health", response_model=CorpusHealthResponse)
def get_health(session: Session = Depends(get_session)) -> CorpusHealthResponse:
    return health(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient
    from datetime import datetime, timezone

    that_app = FastAPI()
    that_app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)
    t_session = TestingSession()

    t_session.add(AskCorpusDoc(
        content_hash="abc123def456",
        indexed_at=datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
        server_id="srv-001",
        snippet="Python async patterns",
        terms='["python", "async", "fastapi"]',
    ))
    t_session.add(AskCorpusDoc(
        content_hash="xyz789abc123",
        indexed_at=datetime(2024, 1, 20, 14, 0, 0, tzinfo=timezone.utc),
        server_id="srv-002",
        snippet="SQLAlchemy ORM usage",
        terms='["sqlalchemy", "orm", "database"]',
    ))
    t_session.add(AskCorpusDoc(
        content_hash="abc123def456",
        indexed_at=datetime(2024, 1, 25, 9, 15, 0, tzinfo=timezone.utc),
        server_id="srv-003",
        snippet="More Python patterns",
        terms='["python", "patterns", "api"]',
    ))
    t_session.commit()

    that_app.dependency_overrides[get_session] = lambda: t_session

    client = TestClient(that_app)
    resp = client.get("/api/ask/corpus/health")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

    data = resp.json()
    assert "size" in data, "Missing 'size' field"
    assert "last_indexed_at" in data, "Missing 'last_indexed_at' field"
    assert "term_distribution" in data, "Missing 'term_distribution' field"
    assert "hash_distribution" in data, "Missing 'hash_distribution' field"

    assert data["size"] == 3, f"Expected size=3, got {data['size']}"
    assert data["last_indexed_at"] is not None, "Expected last_indexed_at to be set"
    assert "python" in data["term_distribution"], "Expected 'python' in term_distribution"
    assert data["term_distribution"]["python"] == 2, f"Expected python count=2, got {data['term_distribution']['python']}"
    assert "abc123de" in data["hash_distribution"], "Expected 'abc123de' in hash_distribution"
    assert data["hash_distribution"]["abc123de"] == 2, f"Expected hash count=2, got {data['hash_distribution']['abc123de']}"

    print("PASS")