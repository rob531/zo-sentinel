"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Generator, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import AskCorpusDoc, Base

from .logic import corpus_staleness

router = APIRouter(prefix="/api", tags=["ask_corpus_staleness_watch"])


class StaleItem(BaseModel):
    server_id: str
    snippet: str
    indexed_at: str
    age_days: int
    terms_count: int


class Summary(BaseModel):
    total_docs: int
    stale_docs: int
    stale_pct: float
    oldest_indexed_at: Optional[str] = None
    newest_indexed_at: Optional[str] = None


class CorpusStalenessResponse(BaseModel):
    stale_days: int
    summary: Summary
    stale: List[StaleItem]


@router.get("/ask/corpus-staleness", response_model=CorpusStalenessResponse)
def get_corpus_staleness(db: Session = Depends(get_session)) -> CorpusStalenessResponse:
    result = corpus_staleness(db)
    return CorpusStalenessResponse(**result)


# --------------------------------------------------------------------------- #
# __main__ self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # In‑memory SQLite setup
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def get_test_session() -> Generator[Session, None, None]:
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    # Seed data
    now = datetime.utcnow()
    old_server = "old-server-1"
    today_server_ids = [f"today-server-{i}" for i in range(1, 4)]

    with TestSession() as sess:
        # old document (30 days ago) with 3 terms
        sess.add(
            AskCorpusDoc(
                server_id=old_server,
                snippet="old snippet",
                terms=["term1", "term2", "term3"],
                content_hash="hash-old",
                indexed_at=now - timedelta(days=30),
            )
        )
        # three fresh documents (today) with empty terms
        for sid in today_server_ids:
            sess.add(
                AskCorpusDoc(
                    server_id=sid,
                    snippet="fresh snippet",
                    terms=[],
                    content_hash=f"hash-{sid}",
                    indexed_at=now,
                )
            )
        sess.commit()

    # Build FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/ask/corpus-staleness")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    # Assertions per contract
    assert data["summary"]["total_docs"] == 4, "total_docs mismatch"
    assert data["summary"]["stale_docs"] == 1, "stale_docs mismatch"
    assert data["summary"]["stale_pct"] == 25.0, "stale_pct mismatch"
    assert data["stale"][0]["server_id"] == old_server, "old server id mismatch"
    assert data["stale"][0]["terms_count"] == 3, "terms_count mismatch"

    print("PASS")