# services/staged/ask_retrieval_service/contract.py
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

# Real data layer imports (must not be re‑defined)
from app.db import get_session, Base
from app.models import AskCorpusDoc, McpServerRegistry

router = APIRouter(prefix="/api")


class AskResult(BaseModel):
    server_id: str
    name: str
    snippet: str
    relevance_score: float
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None


class AskSearchResponse(BaseModel):
    query: str
    total: int
    results: List[AskResult]


def _score_document(query_terms: List[str], doc: AskCorpusDoc) -> float:
    """Very simple TF‑style overlap scoring."""
    doc_terms = set(t.lower() for t in (doc.terms or []))
    snippet_terms = set(word.lower() for word in (doc.snippet or "").split())
    overlap = set(query_terms) & (doc_terms | snippet_terms)
    return float(len(overlap))


@router.get(
    "/ask/search",
    response_model=AskSearchResponse,
    responses={404: {"description": "No results"}},
)
def search_ask(
    q: str = Query(..., alias="q"),
    limit: int = Query(10, ge=1),
    session: Session = Depends(get_session),
):
    query_terms = [t.lower() for t in q.split()]
    stmt = (
        select(AskCorpusDoc, McpServerRegistry)
        .join(
            McpServerRegistry,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
    )
    rows = session.execute(stmt).all()
    if not rows:
        raise HTTPException(status_code=404, detail="No results")

    scored = []
    for doc, server in rows:
        score = _score_document(query_terms, doc)
        scored.append(
            {
                "server_id": server.server_id,
                "name": server.name,
                "snippet": doc.snippet,
                "relevance_score": score,
                "risk_tier": server.risk_tier,
                "verdict": server.verdict,
            }
        )
    # sort by relevance_score descending
    scored.sort(key=lambda x: x["relevance_score"], reverse=True)

    total = len(scored)
    limited = scored[:limit]

    return AskSearchResponse(query=q, total=total, results=limited)


# --------------------------------------------------------------------------- #
# Self‑test (executed via `python -m services.staged.ask_retrieval_service.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Build an in‑memory SQLite DB that mimics the real schema
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Dependency override for the test app
    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # Seed test data
    with SessionLocal() as sess:
        servers = [
            McpServerRegistry(
                server_id="srv1",
                name="Server One",
                risk_tier="high",
                verdict="malicious",
            ),
            McpServerRegistry(
                server_id="srv2",
                name="Server Two",
                risk_tier="medium",
                verdict="suspicious",
            ),
            McpServerRegistry(
                server_id="srv3",
                name="Server Three",
                risk_tier="low",
                verdict="clean",
            ),
        ]
        docs = [
            AskCorpusDoc(
                server_id="srv1",
                snippet="This is a test snippet about malware",
                terms=["malware", "test"],
            ),
            AskCorpusDoc(
                server_id="srv2",
                snippet="Another snippet about phishing",
                terms=["phishing"],
            ),
            AskCorpusDoc(
                server_id="srv3",
                snippet="Irrelevant content",
                terms=["unrelated"],
            ),
        ]
        sess.add_all(servers + docs)
        sess.commit()

    client = TestClient(app)

    # Perform the request
    response = client.get("/api/ask/search?q=malware%20test&limit=2")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()
    assert data["query"] == "malware test"
    assert data["total"] == 3
    assert len(data["results"]) == 2
    top = data["results"][0]
    assert top["server_id"] == "srv1", f"Top result server_id {top['server_id']} != srv1"

    print("PASS")