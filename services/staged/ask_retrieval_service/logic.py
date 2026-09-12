from typing import Any
from fastapi import Depends
from sqlalchemy import text, column, table, func, literal_column
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry

from write_service import fetch


def retrieve_ask_corpus(
    q: str,
    server_ids: list[str] | None = None,
    limit: int = 10,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    corpus = table("ask_corpus_index")
    registry = table("McpServerRegistry")

    join_cond = corpus.c.server_id == registry.c.server_id

    try:
        ts_rank_expr = func.ts_rank(corpus.c.terms.op("::text")("english"), func.to_tsquery("english", q))
        select_cols = [
            corpus.c.server_id,
            registry.c.name,
            corpus.c.snippet,
            registry.c.risk_tier,
            registry.c.verdict,
            ts_rank_expr.label("relevance_score"),
        ]
        order_col = ts_rank_expr.desc()
        search_cond = func.to_tsquery("english", q).op("@@")(func.to_tsvector("english", corpus.c.terms.op("::text")))
    except Exception:
        ts_rank_expr = func.length(corpus.c.terms)
        select_cols = [
            corpus.c.server_id,
            registry.c.name,
            corpus.c.snippet,
            registry.c.risk_tier,
            registry.c.verdict,
            ts_rank_expr.label("relevance_score"),
        ]
        order_col = literal_column("relevance_score").desc()
        search_cond = corpus.c.terms.ilike(f"%{q}%")

    query = (
        session.query(
            corpus.c.server_id,
            registry.c.name,
            corpus.c.snippet,
            registry.c.risk_tier,
            registry.c.verdict,
            ts_rank_expr.label("relevance_score"),
        )
        .select_from(corpus.join(registry, join_cond))
        .filter(search_cond)
        .order_by(order_col)
        .limit(limit)
    )

    if server_ids:
        query = query.filter(corpus.c.server_id.in_(server_ids))

    rows = query.all()

    return {
        "query": q,
        "results": [
            {
                "server_id": row.server_id,
                "name": row.name,
                "snippet": row.snippet,
                "risk_tier": row.risk_tier,
                "verdict": row.verdict,
                "relevance_score": float(row.relevance_score),
            }
            for row in rows
        ],
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS McpServerRegistry (
                server_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                risk_tier TEXT,
                verdict TEXT,
                confidence REAL,
                description TEXT,
                first_seen TEXT,
                last_assessed TEXT,
                last_scanned TEXT,
                last_seen TEXT,
                meta TEXT,
                registry_source TEXT,
                scan_count INTEGER,
                trust_score REAL,
                url TEXT,
                verdict_reasoning TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS ask_corpus_index (
                content_hash TEXT PRIMARY KEY,
                indexed_at TEXT,
                server_id TEXT NOT NULL,
                snippet TEXT NOT NULL,
                terms TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO McpServerRegistry (server_id, name, risk_tier, verdict)
            VALUES
                ('srv-001', 'AuthService Alpha', 'low', 'approved'),
                ('srv-002', 'AuthService Beta', 'medium', 'pending'),
                ('srv-003', 'AuthService Gamma', 'high', 'rejected')
        """))
        conn.execute(text("""
            INSERT INTO ask_corpus_index (content_hash, indexed_at, server_id, snippet, terms)
            VALUES
                ('hash001', '2024-01-01', 'srv-001', 'OAuth2 authentication service', '["oauth2", "authentication", "oauth"]'),
                ('hash002', '2024-01-01', 'srv-002', 'JWT token issuer', '["jwt", "token", "issuer"]'),
                ('hash003', '2024-01-01', 'srv-003', 'SAML authentication provider', '["saml", "authentication", "sso"]')
        """))

    app = FastAPI()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    with app.app.context():
        from app.db import get_session as _get_session
        db = next(_get_session())

        result = retrieve_ask_corpus(
            q="authentication",
            server_ids=["srv-001", "srv-002", "srv-003"],
            limit=10,
            session=db,
        )

        assert result["query"] == "authentication"
        assert len(result["results"]) > 0, "results should be non-empty"
        server_ids_returned = [r["server_id"] for r in result["results"]]
        assert "srv-001" in server_ids_returned or "srv-003" in server_ids_returned, f"expected srv-001 or srv-003 in results, got {server_ids_returned}"

        print("PASS")