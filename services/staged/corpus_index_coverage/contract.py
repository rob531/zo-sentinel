from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


router = APIRouter(prefix="/api")


def _term_count(terms: Any) -> int:
    if terms is None:
        return 0
    if isinstance(terms, (list, tuple, set, dict)):
        return len(terms)
    return 1


@router.get("/corpus/coverage")
def corpus_coverage(
    limit: int = Query(default=20),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return corpus coverage for the MCP server registry."""
    bounded_limit = max(0, limit)

    coverage_row = session.execute(
        select(
            func.count(distinct(McpServerRegistry.server_id)),
            func.count(distinct(AskCorpusDoc.server_id)),
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            AskCorpusDoc,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
    ).one()

    total_servers = int(coverage_row[0] or 0)
    indexed_servers = int(coverage_row[1] or 0)
    coverage_pct = (
        round((indexed_servers / total_servers) * 100, 2)
        if total_servers
        else 0.0
    )

    missing: list[dict[str, Any]] = []
    if bounded_limit > 0:
        missing_rows = session.execute(
            select(
                McpServerRegistry.server_id,
                McpServerRegistry.name,
                McpServerRegistry.risk_tier,
            )
            .select_from(McpServerRegistry)
            .outerjoin(
                AskCorpusDoc,
                AskCorpusDoc.server_id == McpServerRegistry.server_id,
            )
            .where(AskCorpusDoc.server_id.is_(None))
            .order_by(McpServerRegistry.server_id)
            .limit(bounded_limit)
        ).all()
        missing = [
            {
                "server_id": row.server_id,
                "name": row.name,
                "risk_tier": row.risk_tier,
            }
            for row in missing_rows
        ]

    top_indexed: list[dict[str, Any]] = []
    if bounded_limit > 0:
        indexed_rows = session.execute(
            select(
                AskCorpusDoc.server_id,
                AskCorpusDoc.snippet,
                AskCorpusDoc.terms,
                AskCorpusDoc.indexed_at,
            ).order_by(AskCorpusDoc.server_id)
        ).all()
        ranked = [
            {
                "server_id": row.server_id,
                "term_count": _term_count(row.terms),
                "snippet_chars": len(row.snippet or ""),
                "indexed_at": row.indexed_at,
            }
            for row in indexed_rows
        ]
        ranked.sort(key=lambda item: (-item["term_count"], item["server_id"]))
        top_indexed = ranked[:bounded_limit]

    return {
        "generated_at": datetime.now(timezone.utc),
        "limit": limit,
        "total_servers": total_servers,
        "indexed_servers": indexed_servers,
        "coverage_pct": coverage_pct,
        "missing": missing,
        "top_indexed": top_indexed,
    }


app = FastAPI()
app.include_router(router)


def _self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(
        engine,
        tables=[McpServerRegistry.__table__, AskCorpusDoc.__table__],
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Alpha",
                    risk_tier="low",
                    verdict="approved",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Beta",
                    risk_tier="medium",
                    verdict="approved",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Gamma",
                    risk_tier="high",
                    verdict="review",
                ),
                AskCorpusDoc(
                    server_id="server-1",
                    snippet="alpha documentation",
                    terms=["alpha", "documentation"],
                    content_hash="hash-1",
                ),
                AskCorpusDoc(
                    server_id="server-2",
                    snippet="beta documentation",
                    terms=["beta"],
                    content_hash="hash-2",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/corpus/coverage?limit=10")
        assert response.status_code == 200
        payload = response.json()
        assert payload["indexed_servers"] == 2
        assert 0 <= payload["coverage_pct"] <= 100
        assert len(payload["missing"]) == 1
        assert payload["missing"][0]["server_id"] == "server-3"

    engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")
    raise SystemExit(0)