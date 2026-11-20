"""verdict_export_router.py -- CSV audit export endpoints for servers and filtered portfolios."""
from __future__ import annotations

import csv
from io import StringIO
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from clerk_m2m_auth import Principal, get_principal

router = APIRouter(tags=["export"])


class AxisScore(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float


class VerdictExportResponse(BaseModel):
    scores: List[AxisScore]


def _render_server_csv(db: Session, server_id: str, include_headers: bool = True) -> Response:
    scores = db.query(McpLlmAxisScore).filter(
        McpLlmAxisScore.server_id == server_id
    ).all()
    if not scores:
        raise HTTPException(status_code=404, detail="Server not found")

    output = StringIO()
    writer = csv.writer(output)
    if include_headers:
        writer.writerow(["axis_name", "label", "p_top", "p_critical", "p_danger"])
    for score in scores:
        writer.writerow([
            score.axis_name,
            score.label,
            score.p_top,
            score.p_critical,
            score.p_danger,
        ])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{server_id}.csv"'},
    )


@router.get("/export/{server_id}")
def get_verdict_export(
    server_id: str,
    include_headers: bool = True,
    db: Session = Depends(get_session),
) -> Response:
    """Export per-server 7-axis rows as CSV."""
    return _render_server_csv(db, server_id, include_headers=include_headers)


@router.get("/api/export/server/{server_id}.csv")
def export_server_csv(
    server_id: str,
    include_headers: bool = True,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> Response:
    """Authenticated CSV export of a single server's risk axes."""
    return _render_server_csv(db, server_id, include_headers=include_headers)


@router.get("/api/export/portfolio.csv")
def export_portfolio_csv(
    risk: str = "",
    source: str = "",
    limit: int = 250,
    db: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> Response:
    """Authenticated CSV export of registry servers (filtered optionally by tier/source)."""
    bounded = max(1, min(int(limit), 1000))
    stmt = select(McpServerRegistry)
    if risk.strip():
        stmt = stmt.where(func.upper(McpServerRegistry.risk_tier) == risk.strip().upper())
    if source.strip():
        stmt = stmt.where(McpServerRegistry.registry_source == source.strip())
    rows = db.execute(stmt.limit(bounded)).scalars().all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["server_id", "name", "registry_source", "risk_tier", "verdict", "trust_score", "url"])
    for r in rows:
        writer.writerow([
            r.server_id,
            r.name or "",
            r.registry_source or "",
            r.risk_tier or "unassessed",
            r.verdict or "",
            r.trust_score if r.trust_score is not None else "",
            r.url or "",
        ])
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="mcprisky_portfolio.csv"'},
    )


if __name__ == "__main__":
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    def test_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = test_get_session
    app.dependency_overrides[get_principal] = lambda: Principal(user_id="exp-admin", role="admin")

    with TestSession() as session:
        session.add(McpServerRegistry(server_id="test-server-123", name="Demo MCP", registry_source="github", risk_tier="HIGH", url="https://github.com/org/demo"))
        session.add_all([
            McpLlmAxisScore(id=1, server_id="test-server-123", axis_name="overall_risk", label="HIGH", model_version="v3", p_top=0.9, p_critical=0.8, p_danger=0.7),
            McpLlmAxisScore(id=2, server_id="test-server-123", axis_name="auth_strength", label="WEAK", model_version="v3", p_top=0.85, p_critical=0.75, p_danger=0.65),
        ])
        session.commit()

    client = TestClient(app)
    r1 = client.get("/export/test-server-123")
    assert r1.status_code == 200 and r1.headers["content-type"].startswith("text/csv")
    lines = r1.text.splitlines()
    assert len(lines) >= 2 and lines[0] == "axis_name,label,p_top,p_critical,p_danger"

    r2 = client.get("/api/export/server/test-server-123.csv")
    assert r2.status_code == 200 and "overall_risk,HIGH" in r2.text

    r3 = client.get("/api/export/portfolio.csv?risk=HIGH")
    assert r3.status_code == 200 and "test-server-123,Demo MCP,github,HIGH" in r3.text
    print("PASS")
