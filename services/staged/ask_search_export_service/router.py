"""ask_search_export_service router."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
import csv
import io

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry

router = APIRouter(prefix="/api", tags=["ask"])


@router.get("/ask/search/export")
async def export_search_results(
    query: str = Query(default=""),
    perspective_id: Optional[int] = Query(default=None),
    format: str = Query(default="json", pattern="^(csv|json)$"),
    session: Session = Depends(get_session),
):
    stmt = (
        session.query(
            AskCorpusDoc.server_id,
            AskCorpusDoc.snippet,
            AskCorpusDoc.terms,
            AskCorpusDoc.content_hash,
            AskCorpusDoc.indexed_at,
            McpServerRegistry.name,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        )
        .outerjoin(McpServerRegistry, AskCorpusDoc.server_id == McpServerRegistry.server_id)
    )

    if query:
        search_term = f"%{query}%"
        stmt = stmt.filter(
            (AskCorpusDoc.terms.ilike(search_term)) | (AskCorpusDoc.snippet.ilike(search_term))
        )

    if perspective_id is not None:
        stmt = stmt.filter(McpServerRegistry.server_id == perspective_id)

    stmt = stmt.order_by(AskCorpusDoc.indexed_at.desc()).limit(500)
    results = stmt.all()

    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["server_id", "name", "snippet", "terms", "content_hash", "indexed_at", "verdict", "risk_tier"])
        for row in results:
            writer.writerow(
                [
                    row.server_id,
                    row.name or "",
                    row.snippet,
                    row.terms,
                    row.content_hash,
                    row.indexed_at.isoformat() if row.indexed_at else "",
                    row.verdict or "",
                    row.risk_tier or "",
                ]
            )
        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=ask_search_export.csv"},
        )

    return JSONResponse(
        content=[
            {
                "server_id": row.server_id,
                "name": row.name or "",
                "snippet": row.snippet,
                "terms": row.terms,
                "content_hash": row.content_hash,
                "indexed_at": row.indexed_at.isoformat() if row.indexed_at else None,
                "verdict": row.verdict or "",
                "risk_tier": row.risk_tier or "",
            }
            for row in results
        ]
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE ask_corpus_index ("
                "content_hash TEXT, indexed_at TEXT, server_id INTEGER, snippet TEXT, terms TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE mcp_server_registry ("
                "server_id INTEGER, name TEXT, verdict TEXT, risk_tier TEXT)"
            )
        )
        conn.commit()

    def get_test_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    session = SessionLocal()
    c1 = AskCorpusDoc(terms="security vulnerability breach", snippet="Security flaw detected", content_hash="ch1", server_id=1, indexed_at=datetime.now())
    c2 = AskCorpusDoc(terms="security audit compliance", snippet="Security audit passed", content_hash="ch2", server_id=2, indexed_at=datetime.now())
    c3 = AskCorpusDoc(terms="api endpoint rate limit", snippet="API rate limiting", content_hash="ch3", server_id=3, indexed_at=datetime.now())
    c4 = AskCorpusDoc(terms="authentication oauth2 jwt", snippet="OAuth2 implementation", content_hash="ch4", server_id=4, indexed_at=datetime.now())
    c5 = AskCorpusDoc(terms="database migration schema", snippet="Schema update", content_hash="ch5", server_id=5, indexed_at=datetime.now())
    session.add_all([c1, c2, c3, c4, c5])
    session.commit()
    session.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    with TestClient(app) as client:
        r1 = client.get("/api/ask/search/export?query=security&format=csv")
        assert r1.status_code == 200, f"CSV status: {r1.status_code}"
        assert "server_id" in r1.text.split("\n")[0]

        r2 = client.get("/api/ask/search/export?query=security&format=json")
        assert r2.status_code == 200, f"JSON status: {r2.status_code}"
        data2 = r2.json()
        assert isinstance(data2, list)
        assert len(data2) >= 2, f"JSON length: {len(data2)}"

        print("PASS")