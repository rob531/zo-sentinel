# deps: fastapi, sqlalchemy
"""
FastAPI router exposing GET /health/ask-corpus returning corpus statistics.
Mirrors the structure of verdict_view_api.py.
"""
from __future__ import annotations

import sys as _sys
from os.path import abspath, dirname, join as _pj

_repo_root = abspath(_pj(dirname(dirname(dirname(__file__)))))
if _repo_root not in _sys.path:
    _sys.path.insert(0, _repo_root)

from datetime import datetime, timezone
from typing import Dict, Any

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

# Import the real data layer – never create a mock session here.
from app.db import get_session, Base
from app.models import AskCorpusDoc

router = APIRouter()


@router.get(
    "/health/ask-corpus",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Corpus health statistics",
    tags=["health"],
)
async def get_ask_corpus_health(db: "Session" = Depends(get_session)) -> Dict[str, Any]:
    """Return statistics about the Ask‑Corpus index.

    * ``total_docs`` – total rows in ``ask_corpus_index``.
    * ``unique_servers`` – distinct ``server_id`` count.
    * ``last_indexed`` – ISO‑8601 timestamp of the newest ``indexed_at``.
    * ``index_age_seconds`` – seconds elapsed since ``last_indexed``.
    """
    # total rows
    total_stmt = select(func.count()).select_from(AskCorpusDoc)
    total_docs = db.execute(total_stmt).scalar_one()

    # distinct servers
    uniq_stmt = select(func.count(func.distinct(AskCorpusDoc.server_id)))
    unique_servers = db.execute(uniq_stmt).scalar_one()

    # most recent indexed_at
    recent_stmt = (
        select(AskCorpusDoc.indexed_at)
        .order_by(AskCorpusDoc.indexed_at.desc())
        .limit(1)
    )
    last_indexed_row = db.execute(recent_stmt).scalar_one_or_none()

    if last_indexed_row is None:
        # No rows – return zeros and null timestamps.
        return {
            "total_docs": 0,
            "unique_servers": 0,
            "last_indexed": None,
            "index_age_seconds": None,
        }

    # Ensure datetime is timezone‑aware for subtraction.
    if last_indexed_row.tzinfo is None:
        last_indexed = last_indexed_row.replace(tzinfo=timezone.utc)
    else:
        last_indexed = last_indexed_row

    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    age_seconds = int((now - last_indexed).total_seconds())

    return {
        "total_docs": total_docs,
        "unique_servers": unique_servers,
        "last_indexed": last_indexed.isoformat(),
        "index_age_seconds": age_seconds,
    }


# ---------------------------------------------------------------------------
# Self‑test – executed when the module is run directly.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Add project root so 'app' package resolves.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from datetime import timedelta

    # Build a throwaway SQLite engine.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables.
    Base.metadata.create_all(bind=engine)

    # Override the dependency.
    def _override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    # Seed a couple of rows using only required constructor kwargs.
    with SessionLocal() as db:
        now = datetime.utcnow().replace(tzinfo=timezone.utc)
        doc1 = AskCorpusDoc(server_id="srv-1", indexed_at=now)
        doc2 = AskCorpusDoc(server_id="srv-2", indexed_at=now - timedelta(hours=1))
        db.add_all([doc1, doc2])
        db.commit()

    client = TestClient(app)
    resp = client.get("/health/ask-corpus")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}")
        sys.exit(1)
    data = resp.json()
    # Basic sanity checks.
    if not all(k in data for k in ("total_docs", "unique_servers", "last_indexed", "index_age_seconds")):
        print("FAIL: missing keys in response")
        sys.exit(1)
    if data["total_docs"] < 0 or data["unique_servers"] < 0 or data["index_age_seconds"] < 0:
        print("FAIL: negative values in response")
        sys.exit(1)
    print("PASS")
