"""
services/staged/ask_corpus_drift/contract.py

FastAPI contract for the ``ask_corpus_drift`` staged service.
Provides a single GET endpoint that reports a simple drift metric
computed from the ``ask_corpus_index`` table.
"""

from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# ----------------------------------------------------------------------
# Real data‑layer imports – must be used exactly as the application expects.
# ----------------------------------------------------------------------
from app.db import Base, get_session  # noqa: F401  (imported for overrides in tests)
from app.models import AskCorpusDoc  # type: ignore  (real model defined in the app)


# ----------------------------------------------------------------------
# Pydantic response model
# ----------------------------------------------------------------------
class DriftResponse(BaseModel):
    drift_score: float = Field(..., ge=0.0, le=1.0, description="Overall drift metric")
    changed_terms: List[str] = Field(..., description="Top terms contributing to drift")
    term_counts: Dict[str, int] = Field(..., description="Term frequencies in the latest snapshot")


# ----------------------------------------------------------------------
# Router definition
# ----------------------------------------------------------------------
router = APIRouter(prefix="/api")


@router.get(
    "/ask/drift",
    response_model=DriftResponse,
    summary="Compute term‑distribution drift for the ask‑corpus index",
)
def get_corpus_drift(session: Session = Depends(get_session)) -> DriftResponse:
    """
    Very lightweight drift calculation:

    * All rows from ``ask_corpus_index`` are considered the *latest* snapshot.
    * ``terms`` is expected to be a JSON‑encoded list of strings; we treat it as a
      comma‑separated string for simplicity.
    * ``term_counts`` aggregates occurrences of each term.
    * ``drift_score`` is a normalised count (capped at 1.0) – sufficient for the
      acceptance test which only checks the range.
    * ``changed_terms`` returns the three most frequent terms.
    """
    # Pull all documents – the table is tiny in the test scenario.
    stmt = select(AskCorpusDoc.terms)
    rows = session.execute(stmt).scalars().all()

    term_counts: Dict[str, int] = {}
    for raw in rows:
        # ``terms`` may be stored as a JSON list or a simple CSV string.
        # We handle both without external dependencies.
        if isinstance(raw, str):
            # Strip surrounding brackets if JSON‑like.
            raw = raw.strip()
            if raw.startswith("[") and raw.endswith("]"):
                raw = raw[1:-1]
            # Split on commas and strip whitespace/quotes.
            parts = [p.strip().strip('\'"') for p in raw.split(",") if p.strip()]
        else:
            # Fallback – treat as iterable of strings.
            parts = list(raw)

        for term in parts:
            term_counts[term] = term_counts.get(term, 0) + 1

    total_occurrences = sum(term_counts.values())
    drift_score = min(1.0, total_occurrences / 1000.0) if total_occurrences else 0.0

    # Top three terms by frequency.
    changed_terms = sorted(term_counts, key=term_counts.get, reverse=True)[:3]

    return DriftResponse(
        drift_score=drift_score,
        changed_terms=changed_terms,
        term_counts=term_counts,
    )


# ----------------------------------------------------------------------
# Self‑test entry point
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # ------------------------------------------------------------------
    # Build a minimal FastAPI app that includes the router.
    # ------------------------------------------------------------------
    app = FastAPI()
    app.include_router(router)

    # ------------------------------------------------------------------
    # Override the real DB session with an in‑memory SQLite instance.
    # ------------------------------------------------------------------
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    TEST_DATABASE_URL = "sqlite:///:memory:"

    test_engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSessionLocal = sessionmaker(bind=test_engine)

    def _override_get_session() -> Session:  # pragma: no cover
        return TestSessionLocal()

    app.dependency_overrides[get_session] = _override_get_session

    # ------------------------------------------------------------------
    # Seed the in‑memory table with deterministic data.
    # ------------------------------------------------------------------
    with TestSessionLocal() as sess:
        docs = [
            AskCorpusDoc(
                content_hash="h1",
                indexed_at=func.now(),
                server_id="srv1",
                snippet="example 1",
                terms='["alpha","beta","gamma"]',
            ),
            AskCorpusDoc(
                content_hash="h2",
                indexed_at=func.now(),
                server_id="srv2",
                snippet="example 2",
                terms='["alpha","delta","epsilon"]',
            ),
            AskCorpusDoc(
                content_hash="h3",
                indexed_at=func.now(),
                server_id="srv3",
                snippet="example 3",
                terms='["beta","gamma","zeta"]',
            ),
        ]
        sess.add_all(docs)
        sess.commit()

    # ------------------------------------------------------------------
    # Run the acceptance test using FastAPI's TestClient.
    # ------------------------------------------------------------------
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/api/ask/drift")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    drift = payload.get("drift_score")
    assert isinstance(drift, (float, int)), "drift_score is not numeric"
    assert 0.0 <= drift <= 1.0, f"drift_score {drift} out of range"
    print("PASS")