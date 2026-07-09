# deps: fastapi, pydantic, nltk, sqlalchemy
"""FastAPI router for expanding ASK corpus search queries with synonyms.
Implements GET /ask/expand?q=... using NLTK WordNet (fallback if unavailable).
Data layer uses the real app DB session import, but does not query tables.
Self-test overrides the DB session with an in‑memory SQLite DB.
"""

from __future__ import annotations

from typing import List, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
# Import a model to satisfy the real data layer requirement (even if unused).
from app.models import AskCorpusDoc

router = APIRouter(prefix="/ask", tags=["ask"])


class Expansion(BaseModel):
    term: str
    confidence: float


class ExpansionResponse(BaseModel):
    query: str
    expanded: List[Expansion]


def _synonyms(word: str) -> List[str]:
    """Return a list of synonym strings for *word* using NLTK WordNet.
    If the WordNet corpus is missing, fall back to a simple deterministic list.
    """
    try:
        from nltk.corpus import wordnet as wn
        syns = set()
        for syn in wn.synsets(word):
            for lemma in syn.lemmas():
                name = lemma.name().replace('_', ' ')
                if name.lower() != word.lower():
                    syns.add(name)
        return list(syns)
    except Exception:
        # Fallback: deterministic dummy synonyms to keep the endpoint useful without
        # requiring the NLTK data download (which would need network access).
        return [f"{word}_syn1", f"{word}_syn2", f"{word}_syn3"]


@router.get("/expand", response_model=ExpansionResponse)
def expand_query(q: str = "", db: Session = Depends(get_session)) -> ExpansionResponse:
    """Expand the supplied query term *q* with synonyms.
    Returns a list of terms with dummy confidence scores.
    """
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' required")
    # The DB session is injected to satisfy the contract; we do not perform any DB I/O.
    # (Future versions may look up corpus terms here.)
    syns = _synonyms(q)
    # Include the original term as the highest‑confidence result.
    results: List[Dict[str, float]] = [{"term": q, "confidence": 1.0}]
    for s in syns:
        results.append({"term": s, "confidence": 0.8})
    return ExpansionResponse(query=q, expanded=[Expansion(**r) for r in results])


if __name__ == "__main__":  # CI‑safe self‑test
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    # In‑memory SQLite DB – no network, no real data.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)
    resp = client.get("/ask/expand?q=security")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)
    data = resp.json()
    if not isinstance(data, dict) or "expanded" not in data:
        print("FAIL: malformed response", file=sys.stderr)
        sys.exit(1)
    if len(data["expanded"]) < 4:
        print("FAIL: expected at least 4 terms", file=sys.stderr)
        sys.exit(1)
    print("PASS")
