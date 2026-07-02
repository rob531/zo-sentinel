import json
from datetime import datetime
from typing import List, Dict, Any
from fastapi import Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import mcp_server_registry, mcp_llm_axis_scores
from app.services.write_service import write_service_client

def normalize_snippet(server: mcp_server_registry, scores: List[mcp_llm_axis_scores]) -> str:
    axis_labels = [score.axis_label for score in scores]
    snippet = (
        f"{server.name}\n"
        f"{server.description_head}\n"
        f"Verdict: {scores[0].verdict}\n"
        f"Risk Tier: {scores[0].risk_tier}\n"
        f"Axis Labels: {', '.join(axis_labels)}"
    )
    return snippet

def tokenize_terms(snippet: str) -> List[str]:
    return snippet.split()

async def upsert_corpus_index(server_id: int, snippet: str, terms: List[str], session: Session):
    indexed_at = datetime.utcnow()
    data = {
        "server_id": server_id,
        "snippet": snippet,
        "terms": json.dumps(terms),
        "indexed_at": indexed_at
    }
    await write_service_client.write("ask_corpus_index", data)

async def process_server(server: mcp_server_registry, session: Session):
    scores = session.query(mcp_llm_axis_scores).filter_by(server_id=server.id).all()
    if scores:
        snippet = normalize_snippet(server, scores)
        terms = tokenize_terms(snippet)
        await upsert_corpus_index(server.id, snippet, terms, session)

async def build_corpus_index(session: Session):
    servers = session.query(mcp_server_registry).all()
    for server in servers:
        await process_server(server, session)

if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite database for self-test
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create sample data
    db = SessionLocal()
    server1 = mcp_server_registry(name="Server1", description_head="Description1")
    db.add(server1)
    db.commit()

    score1 = mcp_llm_axis_scores(server_id=server1.id, axis_label="Label1", verdict="Verdict1", risk_tier="Tier1")
    score2 = mcp_llm_axis_scores(server_id=server1.id, axis_label="Label2", verdict="Verdict1", risk_tier="Tier1")
    db.add_all([score1, score2])
    db.commit()

    # Run the corpus indexer
    import asyncio
    asyncio.run(build_corpus_index(db))

    # Verify the snippet contains the verdict and an axis label
    result = db.execute("SELECT snippet FROM ask_corpus_index WHERE server_id = :server_id", {"server_id": server1.id}).fetchone()
    assert "Verdict1" in result.snippet and "Label1" in result.snippet, "Snippet does not contain the verdict and an axis label"

    # Run the corpus indexer again to ensure idempotency
    asyncio.run(build_corpus_index(db))
    count = db.execute("SELECT COUNT(*) FROM ask_corpus_index WHERE server_id = :server_id", {"server_id": server1.id}).scalar()
    assert count == 1, "Second run added rows for unchanged input"

    print("PASS")