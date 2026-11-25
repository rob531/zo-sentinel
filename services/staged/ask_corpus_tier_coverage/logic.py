"""logic.py -- the service's data/computation layer (one concern, one file).

Provides read-only coverage statistics for the Ask corpus across MCP risk
 tiers using the real application models and database session.
"""
from __future__ import annotations

from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


def get_tier_coverage(
    db: Session,
    limit: int = 50,
    min_docs: int = 1,
) -> Dict[str, Any]:
    """Return corpus document and server counts grouped by risk tier."""
    limit = max(1, int(limit))
    min_docs = max(1, int(min_docs))

    tier_expr = func.coalesce(McpServerRegistry.risk_tier, "UNSCORED")
    docs_expr = func.count()
    servers_expr = func.count(func.distinct(AskCorpusDoc.server_id))

    rows = db.execute(
        select(
            tier_expr.label("tier"),
            servers_expr.label("servers"),
            docs_expr.label("docs"),
        )
        .select_from(AskCorpusDoc)
        .outerjoin(
            McpServerRegistry,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
        .group_by(tier_expr)
        .having(docs_expr >= min_docs)
        .order_by(docs_expr.desc(), tier_expr.asc())
        .limit(limit)
    ).all()

    total_docs, total_servers = db.execute(
        select(
            func.count(),
            func.count(func.distinct(AskCorpusDoc.server_id)),
        ).select_from(AskCorpusDoc)
    ).one()

    total_docs = int(total_docs or 0)
    total_servers = int(total_servers or 0)

    tiers: List[Dict[str, Any]] = []
    for tier, servers, docs in rows:
        docs = int(docs or 0)
        tiers.append(
            {
                "tier": str(tier or "UNSCORED"),
                "servers": int(servers or 0),
                "docs": docs,
                "doc_pct": round(100.0 * docs / total_docs, 2)
                if total_docs
                else 0.0,
            }
        )

    return {
        "total_docs": total_docs,
        "total_servers": total_servers,
        "tiers": tiers,
    }


# Compatibility aliases for routers that use the service-specific name.
get_corpus_tier_coverage = get_tier_coverage
ask_corpus_tier_coverage = get_tier_coverage


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(test_engine)
    AskCorpusDoc.__table__.create(test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="safe-1", risk_tier="SAFE"),
                McpServerRegistry(server_id="safe-2", risk_tier="SAFE"),
                McpServerRegistry(
                    server_id="danger-1",
                    risk_tier="DANGEROUS",
                ),
                McpServerRegistry(
                    server_id="danger-2",
                    risk_tier="DANGEROUS",
                ),
            ]
        )
        session.add_all(
            [
                AskCorpusDoc(server_id="safe-1", snippet="safe one"),
                AskCorpusDoc(server_id="safe-2", snippet="safe two"),
                AskCorpusDoc(server_id="danger-1", snippet="danger one"),
                AskCorpusDoc(server_id="danger-2", snippet="danger two"),
                AskCorpusDoc(server_id="unscored-1", snippet="unscored"),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/ask/corpus/tier-coverage")
    def tier_coverage(
        limit: int = 50,
        min_docs: int = 1,
        db: Session = __import__("fastapi").Depends(get_session),
    ):
        return get_tier_coverage(db, limit=limit, min_docs=min_docs)

    with TestClient(test_app) as client:
        response = client.get("/api/ask/corpus/tier-coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_docs"] == 5, payload
        assert len(payload["tiers"]) >= 3, payload
        assert any(tier["tier"] == "UNSCORED" for tier in payload["tiers"]), payload
        assert abs(sum(tier["doc_pct"] for tier in payload["tiers"]) - 100.0) < 0.1, payload

    print("PASS")