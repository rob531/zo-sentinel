# services/staged/ask_corpus_health/contract.py
"""
FastAPI contract for the ``ask_corpus_health`` staged service.

Provides a single GET endpoint:
    GET /api/ask/corpus/health

The endpoint returns a JSON payload describing the health of the
``ask_corpus_index`` table.

The module can be executed directly to run a self‑test that builds an
in‑memory SQLite database, seeds it with a minimal record, invokes the
endpoint via ``TestClient`` and prints ``PASS`` on success.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# --------------------------------------------------------------------------- #
# Application data layer – must use the real app models and session factory.
# --------------------------------------------------------------------------- #
from app.db import get_session  # real dependency
from app.models import AskCorpusDoc  # real model (table ``ask_corpus_index``)

# --------------------------------------------------------------------------- #
# Optional business logic – imported if present, otherwise a simple fallback.
# --------------------------------------------------------------------------- #
try:
    from .logic import get_corpus_health  # type: ignore
except Exception:  # pragma: no cover

    def get_corpus_health(db: Session) -> dict:
        """
        Minimal fallback implementation used only when the real logic module
        is unavailable. It aggregates the required fields directly from the
        ``AskCorpusDoc`` table.
        """
        total = db.query(AskCorpusDoc).count()
        last = (
            db.query(AskCorpusDoc.indexed_at)
            .order_by(AskCorpusDoc.indexed_at.desc())
            .limit(1)
            .scalar()
        )
        # Very naive distributions – just count occurrences of each term and hash.
        term_counts: Dict[str, int] = {}
        hash_counts: Dict[str, int] = {}
        for row in db.query(AskCorpusDoc).all():
            for term in (row.terms or []):
                term_counts[term] = term_counts.get(term, 0) + 1
            h = row.content_hash
            hash_counts[h] = hash_counts.get(h, 0) + 1

        return {
            "size": total,
            "last_indexed_at": last or datetime.utcnow(),
            "term_distribution": term_counts,
            "hash_distribution": hash_counts,
        }


# --------------------------------------------------------------------------- #
# Pydantic response model
# --------------------------------------------------------------------------- #
class CorpusHealthResponse(BaseModel):
    size: int
    last_indexed_at: datetime
    term_distribution: Dict[str, int]
    hash_distribution: Dict[str, int]

    class Config:
        orm_mode = True


# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.get(
    "/ask/corpus/health",
    response_model=CorpusHealthResponse,
    summary="Health summary of the Ask Corpus index",
)
def health_endpoint(db: Session = Depends(get_session)):
    """
    Return a health summary for the ``ask_corpus_index`` table.
    """
    result = get_corpus_health(db)
    # Ensure the dict matches the Pydantic model fields
    return CorpusHealthResponse(**result)


# --------------------------------------------------------------------------- #
# Self‑test entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    # Build an in‑memory SQLite engine that mimics the real DB for testing.
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    # Create tables using the real declarative Base from app.models.
    from app.models import Base  # noqa: E402

    Base.metadata.create_all(bind=test_engine)

    # Seed a minimal record.
    def seed():
        with TestSessionLocal() as s:
            doc = AskCorpusDoc(
                content_hash="hash123",
                indexed_at=datetime.utcnow(),
                server_id="srv-1",
                snippet="example snippet",
                terms=["alpha", "beta"],
            )
            s.add(doc)
            s.commit()

    seed()

    # Dependency override that supplies the test session.
    def get_test_session() -> Session:  # pragma: no cover
        with TestSessionLocal() as s:
            yield s

    # Assemble FastAPI app with the router and overrides.
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Run the acceptance test via TestClient.
    from fastapi.testclient import TestClient

    client = TestClient(app)

    resp = client.get("/api/ask/corpus/health")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    expected_keys = {
        "size",
        "last_indexed_at",
        "term_distribution",
        "hash_distribution",
    }
    assert set(payload.keys()) == expected_keys, f"Missing keys: {expected_keys - set(payload)}"
    assert payload["size"] == 1, "Seeded record count mismatch"
    print("PASS")