# services/staged/corpus_term_frequency_api/contract.py
from datetime import datetime
from typing import List, Dict, Any

from fastapi import FastAPI, APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

# Real data layer imports
from app.db import get_session, Base
from app.models import AskCorpusDoc

router = APIRouter(prefix="/api")


class TopTerm(BaseModel):
    term: str
    count: int
    doc_count: int


class CorpusTermFrequencyResponse(BaseModel):
    documents: int = Field(..., description="Total number of documents")
    distinct_terms: int = Field(..., description="Number of distinct terms")
    avg_terms_per_doc: float = Field(..., description="Average number of terms per document")
    empty_term_docs: int = Field(..., description="Documents with empty or null terms")
    top_terms: List[TopTerm] = Field(..., description="Top N terms")


@router.get(
    "/ask/corpus/terms",
    response_model=CorpusTermFrequencyResponse,
    summary="Get top term frequencies for the corpus",
)
def get_corpus_term_frequencies(
    limit: int = Query(10, ge=1, description="Maximum number of top terms to return"),
    db: Session = Depends(get_session),
):
    # Fetch all documents
    docs = db.query(AskCorpusDoc).all()
    total_docs = len(docs)

    if total_docs == 0:
        raise HTTPException(status_code=404, detail="No corpus documents found")

    term_counts: Dict[str, Dict[str, int]] = {}
    total_terms = 0
    empty_term_docs = 0

    for doc in docs:
        terms_raw: Any = getattr(doc, "terms", None)
        if not terms_raw:
            empty_term_docs += 1
            continue

        # Ensure terms is a list
        if not isinstance(terms_raw, list):
            continue

        # Filter only string terms and lower‑case them
        terms = [t.lower() for t in terms_raw if isinstance(t, str)]
        total_terms += len(terms)

        # Count occurrences (including duplicates)
        for term in terms:
            if term not in term_counts:
                term_counts[term] = {"count": 0, "doc_count": 0}
            term_counts[term]["count"] += 1

        # Count per‑document presence
        for term in set(terms):
            term_counts[term]["doc_count"] += 1

    distinct_terms = len(term_counts)
    avg_terms_per_doc = total_terms / total_docs if total_docs else 0.0

    # Sort terms by total count descending, then term name
    sorted_terms = sorted(
        term_counts.items(),
        key=lambda item: (-item[1]["count"], item[0]),
    )

    top_terms = [
        TopTerm(term=term, count=data["count"], doc_count=data["doc_count"])
        for term, data in sorted_terms[:limit]
    ]

    return CorpusTermFrequencyResponse(
        documents=total_docs,
        distinct_terms=distinct_terms,
        avg_terms_per_doc=avg_terms_per_doc,
        empty_term_docs=empty_term_docs,
        top_terms=top_terms,
    )


app = FastAPI()
app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.corpus_term_frequency_api.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Override the dependency to use the in‑memory session
    def get_test_session() -> Session:  # pragma: no cover
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = get_test_session

    # Create tables
    Base.metadata.create_all(bind=engine)

    # Seed test data
    with TestingSessionLocal() as db:
        doc1 = AskCorpusDoc(
            server_id="s1",
            snippet="snippet1",
            terms=["mcp", "auth"],
            content_hash="h1",
            indexed_at=datetime.utcnow(),
        )
        doc2 = AskCorpusDoc(
            server_id="s2",
            snippet="snippet2",
            terms=["mcp", "shell"],
            content_hash="h2",
            indexed_at=datetime.utcnow(),
        )
        doc3 = AskCorpusDoc(
            server_id="s3",
            snippet="snippet3",
            terms=[],
            content_hash="h3",
            indexed_at=datetime.utcnow(),
        )
        db.add_all([doc1, doc2, doc3])
        db.commit()

    client = TestClient(app)

    # Request with limit=1 to verify limit handling
    resp = client.get("/api/ask/corpus/terms?limit=1")
    if resp.status_code != 200:
        print(f"FAIL: Unexpected status {resp.status_code}")
        sys.exit(1)

    data = resp.json()
    try:
        assert data["documents"] == 3
        assert data["empty_term_docs"] == 1
        # Find the "mcp" entry (should be the only entry due to limit=1)
        mcp_entry = next((t for t in data["top_terms"] if t["term"] == "mcp"), None)
        assert mcp_entry is not None, "mcp term missing"
        assert mcp_entry["count"] == 2
        assert mcp_entry["doc_count"] == 2
        assert len(data["top_terms"]) == 1  # limit honoured
    except AssertionError as e:
        print(f"FAIL: {e}")
        sys.exit(1)

    print("PASS")
    sys.exit(0)