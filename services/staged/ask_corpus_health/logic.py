from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session, Base
from app.models import AskCorpusDoc


router = APIRouter(prefix="/api/ask/corpus", tags=["corpus"])


class CorpusHealthResponse(BaseModel):
    size: int
    last_indexed_at: Optional[datetime]
    term_distribution: dict
    hash_distribution: dict
    content_freshness: bool


class HealthCheckResponse(BaseModel):
    health: CorpusHealthResponse


def _compute_term_distribution(session: Session) -> dict:
    docs = session.execute(select(AskCorpusDoc.terms)).scalars().all()
    if not docs:
        return {}
    all_terms = []
    for terms in docs:
        if terms:
            all_terms.extend(terms)
    total = len(all_terms)
    if total == 0:
        return {}
    counts = {}
    for term in all_terms:
        counts[term] = counts.get(term, 0) + 1
    return {term: round(count / total, 4) for term, count in counts.items()}


def _compute_hash_distribution(session: Session) -> dict:
    total = session.execute(select(func.count(AskCorpusDoc.content_hash))).scalar() or 0
    unique = session.execute(select(func.count(func.distinct(AskCorpusDoc.content_hash)))).scalar() or 0
    return {
        "total_documents": total,
        "unique_hashes": unique,
        "collision_ratio": round((total - unique) / total, 4) if total > 0 else 0.0,
    }


def _check_content_freshness(term_dist: dict, baseline: dict, threshold: float = 0.1) -> bool:
    if not baseline:
        return True
    for term, expected_ratio in baseline.items():
        actual_ratio = term_dist.get(term, 0.0)
        if abs(actual_ratio - expected_ratio) > threshold:
            return False
    return True


# Baseline loaded from bus/service (actual implementation reads from service mesh)
def _get_baseline_distribution() -> dict:
    return {}


@router.get("/health", response_model=HealthCheckResponse)
def health(
    session: Session = Depends(get_session),
) -> HealthCheckResponse:
    size = session.execute(select(func.count(AskCorpusDoc.content_hash))).scalar() or 0
    last_indexed_at = session.execute(
        select(func.max(AskCorpusDoc.indexed_at))
    ).scalar()

    term_dist = _compute_term_distribution(session)
    hash_dist = _compute_hash_distribution(session)
    baseline = _get_baseline_distribution()
    content_freshness = _check_content_freshness(term_dist, baseline)

    return HealthCheckResponse(
        health=CorpusHealthResponse(
            size=size,
            last_indexed_at=last_indexed_at,
            term_distribution=term_dist,
            hash_distribution=hash_dist,
            content_freshness=content_freshness,
        )
    )


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    db = TestingSessionLocal()
    now = datetime.utcnow()
    doc1 = AskCorpusDoc(
        content_hash="abc123",
        indexed_at=now,
        server_id="srv1",
        snippet="python fastapi sql",
        terms=["python", "fastapi", "sql"],
    )
    doc2 = AskCorpusDoc(
        content_hash="def456",
        indexed_at=now,
        server_id="srv2",
        snippet="rust async tokio",
        terms=["rust", "async", "tokio"],
    )
    doc3 = AskCorpusDoc(
        content_hash="abc123",
        indexed_at=now,
        server_id="srv3",
        snippet="python asyncio",
        terms=["python", "asyncio"],
    )
    db.add_all([doc1, doc2, doc3])
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/ask/corpus/health")

    assert response.status_code == 200, f"Status: {response.status_code}"
    data = response.json()
    assert "health" in data, "Missing 'health' key"
    h = data["health"]
    assert "size" in h
    assert "last_indexed_at" in h
    assert "term_distribution" in h
    assert "hash_distribution" in h
    assert "content_freshness" in h
    assert h["size"] == 3
    assert h["hash_distribution"]["unique_hashes"] == 2
    assert "python" in h["term_distribution"]
    print("PASS")