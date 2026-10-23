# services/staged/ask_related_queries/contract.py
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List

from sqlalchemy.orm import Session
from sqlalchemy import select

from app.db import get_session, Base
from app.models import AskCorpusDoc

router = APIRouter(prefix="/api")


class Suggestion(BaseModel):
    text: str
    server_id: str
    snippet_preview: str
    relevance_score: float


class RelatedResponse(BaseModel):
    query: str
    suggestions: List[Suggestion]
    total: int


@router.get(
    "/ask/related",
    response_model=RelatedResponse,
    status_code=status.HTTP_200_OK,
)
def get_related(
    query: str,
    limit: int = 5,
    session: Session = Depends(get_session),
) -> RelatedResponse:
    """
    Return related query suggestions based on the AskCorpusDoc index.
    """
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="query parameter is required",
        )

    stmt = (
        select(AskCorpusDoc)
        .where(AskCorpusDoc.terms.ilike(f"%{query}%"))
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()

    suggestions = []
    for row in rows:
        # Simple relevance: 1.0 for any match (placeholder for real scoring)
        relevance = 1.0
        preview = (
            (row.snippet[:100] + "...")
            if len(row.snippet) > 100
            else row.snippet
        )
        suggestions.append(
            Suggestion(
                text=row.terms,
                server_id=row.server_id,
                snippet_preview=preview,
                relevance_score=relevance,
            )
        )

    return RelatedResponse(
        query=query,
        suggestions=suggestions,
        total=len(suggestions),
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.ask_related_queries.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine for the self‑test
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=engine
    )

    # Dependency override for the test session
    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Seed test data
    with TestSessionLocal() as db:
        docs = [
            AskCorpusDoc(
                server_id="srv1",
                snippet="Security is important for every system.",
                terms="security",
                content_hash="h1",
                indexed_at=datetime.utcnow(),
            ),
            AskCorpusDoc(
                server_id="srv2",
                snippet="Network security basics and best practices.",
                terms="network security",
                content_hash="h2",
                indexed_at=datetime.utcnow(),
            ),
            AskCorpusDoc(
                server_id="srv3",
                snippet="Unrelated topic about cooking.",
                terms="cooking",
                content_hash="h3",
                indexed_at=datetime.utcnow(),
            ),
        ]
        db.add_all(docs)
        db.commit()

    # Build FastAPI app with the router and override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    response = client.get(
        "/api/ask/related",
        params={"query": "security", "limit": 5},
    )
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    payload = response.json()
    assert payload["total"] >= 1, "Expected at least one suggestion"
    for sug in payload["suggestions"]:
        assert isinstance(sug["relevance_score"], float), "relevance_score not float"

    print("PASS")
    sys.exit(0)