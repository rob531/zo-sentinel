"""Contract for the ask_corpus_term_ubiquity_api service.

Provides a FastAPI router exposing:
GET /api/ask/corpus/term-ubiquity
which returns the ubiquity (occurrence count) of each term across all
AskCorpusDoc entries.

Running this module as ``python -m services.staged.ask_corpus_term_ubiquity_api.contract``
executes a self‑test that seeds an in‑memory SQLite database, calls the endpoint
via TestClient and validates the response.  On success it prints ``PASS`` and exits
with status 0.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Real data layer imports – do NOT replace with stubs.
from app.db import Base, get_session
from app.models import AskCorpusDoc

router = APIRouter(prefix="/api")


class TermCount(BaseModel):
    term: str
    count: int


class TermUbiquityResponse(BaseModel):
    terms: List[TermCount]


@router.get(
    "/ask/corpus/term-ubiquity",
    response_model=TermUbiquityResponse,
    summary="Return term ubiquity across the ask‑corpus index",
)
def get_term_ubiquity(session: Session = Depends(get_session)) -> TermUbiquityResponse:
    """Count how many documents each term appears in.

    The ``terms`` column of :class:`AskCorpusDoc` is expected to be an iterable
    (e.g. a JSON list).  Each term contributes a single count per document.
    """
    counter: Counter[str] = Counter()
    for doc in session.query(AskCorpusDoc).all():
        # Guard against null / unexpected types.
        if not doc.terms:
            continue
        # Assume ``terms`` is a list/tuple of strings.
        unique_terms = set(doc.terms)  # count each term once per document
        counter.update(unique_terms)

    term_list = [
        TermCount(term=term, count=count) for term, count in sorted(counter.items())
    ]
    return TermUbiquityResponse(terms=term_list)


# --------------------------------------------------------------------------- #
# Self‑test (executed when the module is run as a script)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys

    from fastapi.testclient import TestClient

    # --------------------------------------------------------------------- #
    # Build an in‑memory SQLite engine that mimics the real DB schema.
    # --------------------------------------------------------------------- #
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine
    )

    # Create tables using the real metadata.
    Base.metadata.create_all(bind=test_engine)

    # Dependency override that yields a session bound to the in‑memory DB.
    def _override_get_session() -> Session:  # type: ignore[override]
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # --------------------------------------------------------------------- #
    # Seed three AskCorpusDoc rows with overlapping terms.
    # --------------------------------------------------------------------- #
    seed_session = TestSessionLocal()
    now = datetime.utcnow()
    docs = [
        AskCorpusDoc(
            content_hash="hash1",
            indexed_at=now,
            server_id=1,
            snippet="first snippet",
            terms=["alpha", "beta", "gamma"],
        ),
        AskCorpusDoc(
            content_hash="hash2",
            indexed_at=now,
            server_id=2,
            snippet="second snippet",
            terms=["alpha", "delta"],
        ),
        AskCorpusDoc(
            content_hash="hash3",
            indexed_at=now,
            server_id=3,
            snippet="third snippet",
            terms=["beta", "epsilon"],
        ),
    ]
    seed_session.add_all(docs)
    seed_session.commit()
    seed_session.close()

    # --------------------------------------------------------------------- #
    # Assemble the FastAPI app with the router and the overridden dependency.
    # --------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # --------------------------------------------------------------------- #
    # Execute the request and validate the response.
    # --------------------------------------------------------------------- #
    resp = client.get("/api/ask/corpus/term-ubiquity")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert "terms" in data, "Response missing 'terms' key"
    terms = data["terms"]
    # Expected unique terms: alpha, beta, gamma, delta, epsilon  => 5
    assert len(terms) == 5, f"Expected 5 terms, got {len(terms)}"

    # Find the count for a known term.
    alpha_entry = next((t for t in terms if t["term"] == "alpha"), None)
    assert alpha_entry is not None, "Missing 'alpha' term in response"
    assert (
        alpha_entry["count"] == 2
    ), f"Alpha count expected 2, got {alpha_entry['count']}"

    print("PASS")
    sys.exit(0)