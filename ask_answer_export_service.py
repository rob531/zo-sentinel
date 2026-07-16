"""ask_answer_export_service.py -- CSV export endpoint for the Ask corpus.

GET /ask/search/export?q=&tier=&limit=100
Streams a UTF-8 CSV with Content-Disposition: attachment.
Joins ask_corpus_index with mcp_llm_axis_scores (overall_risk) and
mcp_server_registry (name, url, risk_tier). Applies trust_gate so
official publishers are not shown as false HIGH/CRITICAL.
"""
from __future__ import annotations

import csv
import io
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/ask/search", tags=["ask"])


class ExportParams(BaseModel):
    q: str = ""
    tier: str = ""
    limit: int = 100


def _resolve_overall_risk(server_id: str, db: Session) -> str | None:
    """Fetch the overall_risk label for a server_id from the latest model_version."""
    row = db.execute(
        select(McpLlmAxisScore.label).where(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
        ).order_by(McpLlmAxisScore.scored_at.desc()).limit(1)
    ).scalar_one_or_none()
    return row


def _rows(db: Session, q: str, tier: str, limit: int) -> Generator[dict, None, None]:
    """Yield CSV rows, one dict at a time, applying trust_gate to overall_risk."""
    conds = []
    if q.strip():
        like = f"%{q.strip()}%"
        conds.append(AskCorpusDoc.snippet.ilike(like))
    if not conds:
        conds.append(AskCorpusDoc.server_id.isnot(None))

    stmt = (
        select(AskCorpusDoc, McpServerRegistry)
        .outerjoin(McpServerRegistry, AskCorpusDoc.server_id == McpServerRegistry.server_id)
        .where(and_(*conds))
        .order_by(AskCorpusDoc.indexed_at.desc())
        .limit(limit)
    )
    rows = db.execute(stmt).all()

    for corpus_row, reg_row in rows:
        server_id = corpus_row.server_id
        name = (reg_row.name or "") if reg_row else ""
        url = (reg_row.url or "") if reg_row else ""
        risk_tier = (reg_row.risk_tier or "") if reg_row else ""

        overall_risk = _resolve_overall_risk(server_id, db)
        if overall_risk:
            gate = trust_gate(url, name, {"overall_risk": overall_risk})
            overall_risk = gate.get("published_overall_risk") or overall_risk
        else:
            overall_risk = ""

        snippet = (corpus_row.snippet or "") if corpus_row.snippet else ""
        indexed_at = ""
        if corpus_row.indexed_at:
            indexed_at = corpus_row.indexed_at.isoformat()

        if tier.strip():
            want = tier.strip().upper()
            if overall_risk.upper() != want and risk_tier.upper() != want:
                continue

        yield {
            "server_id": server_id,
            "name": name,
            "snippet": snippet,
            "indexed_at": indexed_at,
            "risk_tier": risk_tier,
            "overall_risk": overall_risk,
            "url": url,
        }


@router.get("/export")
def export_csv(
    q: str = "",
    tier: str = "",
    limit: int = 100,
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Stream a UTF-8 CSV export of the Ask corpus with risk metadata.

    Args:
        q:     optional full-text search over snippet field.
        tier:  optional risk_tier filter (e.g. CRITICAL, HIGH, MEDIUM).
        limit: rows to return, default 100, max 5000.
    """
    limit = max(1, min(limit, 5000))

    def generate() -> Generator[bytes, None, None]:
        buf = io.StringIO()
        writer = csv.DictWriter(
            buf,
            fieldnames=["server_id", "name", "snippet", "indexed_at",
                        "risk_tier", "overall_risk", "url"],
            lineterminator="\n",
        )
        writer.writeheader()
        yield buf.getvalue().encode("utf-8")
        buf.seek(0); buf.truncate(0)

        for row in _rows(db, q, tier, limit):
            writer.writerow(row)
            yield buf.getvalue().encode("utf-8")
            buf.seek(0); buf.truncate(0)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=ask_corpus_export.csv",
            "Cache-Control": "no-store",
        },
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    s = TS()
    s.add(McpServerRegistry(
        server_id="srv-auth",
        name="Auth Service MCP",
        url="https://example.com/auth",
        risk_tier="HIGH",
    ))
    s.add(AskCorpusDoc(
        server_id="srv-auth",
        snippet="Authentication and authorization service for MCP servers",
        indexed_at=None,
    ))
    s.add(McpLlmAxisScore(
        id=1,
        server_id="srv-auth",
        axis_name="overall_risk",
        label="HIGH",
        model_version="v3.0_40974559",
    ))
    s.add(McpServerRegistry(
        server_id="srv-calc",
        name="Calculator MCP",
        url="https://example.com/calc",
        risk_tier="LOW",
    ))
    s.add(AskCorpusDoc(
        server_id="srv-calc",
        snippet="Basic arithmetic operations",
        indexed_at=None,
    ))
    s.add(McpLlmAxisScore(
        id=2,
        server_id="srv-calc",
        axis_name="overall_risk",
        label="LOW",
        model_version="v3.0_40974559",
    ))
    s.commit()
    s.close()

    c = TestClient(app)
    resp = c.get("/ask/search/export?q=auth")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.headers.get("content-type", "").startswith("text/csv"), \
        f"Expected text/csv, got {resp.headers.get('content-type')}"
    assert "attachment" in resp.headers.get("content-disposition", ""), \
        resp.headers.get("content-disposition")
    body = resp.content.decode("utf-8")
    lines = [l for l in body.strip().split("\n") if l]
    assert len(lines) >= 2, f"Expected header + >=1 data row, got {len(lines)} lines: {body!r}"
    header = lines[0].split(",")
    assert "server_id" in header and "overall_risk" in header, header
    data_row = lines[1].split(",")
    assert len(data_row) == 7, f"Expected 7 columns, got {len(data_row)}: {lines[1]!r}"
    print("PASS")
