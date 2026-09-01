# deps: fastapi, pydantic, sqlalchemy
"""FastAPI router extending verdict_view_api with a CSV export endpoint.

GET /verdict/export  -- server verdicts as CSV
Reads server_id, name, risk_tier, overall_risk, and the 7 LLM axis labels + p_top
values from mcp_llm_axis_scores joined to mcp_server_registry.
Optional query params: server_id (single), risk_tier (filter), format=csv (default).
Returns text/csv with Content-Disposition header. No DB writes.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

router = APIRouter(prefix="/api", tags=["verdict"])

AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

CSV_HEADERS = [
    "server_id", "name", "risk_tier",
    "overall_risk",
    "auth_strength_label", "auth_strength_p_top",
    "capability_breadth_label", "capability_breadth_p_top",
    "data_sensitivity_label", "data_sensitivity_p_top",
    "network_egress_label", "network_egress_p_top",
    "maintainer_trust_label", "maintainer_trust_p_top",
    "exploit_surface_label", "exploit_surface_p_top",
]


def _build_axis_map(rows) -> dict:
    out = {}
    for r in rows:
        out[r.axis_name] = r
    return out


def _header_csv() -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_HEADERS)
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=verdicts.csv"},
    )


@router.get("/verdict/export")
def export_verdicts_csv(
    server_id: Optional[str] = Query(None, description="Filter to a single server"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk_tier (low/medium/high)"),
    format: str = Query("csv", description="Output format (csv)"),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Export server verdicts as a CSV document.

    Joins mcp_llm_axis_scores → mcp_server_registry on server_id.
    Applies trust_gate to overall_risk so official publishers are not shown as
    false HIGH/CRITICAL. Accepts optional server_id (exact match) and risk_tier
    (substring filter on the registry's risk_tier column) query params.
    """
    if format.lower() != "csv":
        return Response(status_code=400, content="Only CSV format is supported")

    # Step 1: build candidate server_id list from the registry (handles risk_tier filter
    # before any axis join, so servers with no scores can still be returned).
    reg_q = select(McpServerRegistry)
    if risk_tier:
        reg_q = reg_q.where(McpServerRegistry.risk_tier.ilike(f"%{risk_tier}%"))
    if server_id:
        reg_q = reg_q.where(McpServerRegistry.server_id == server_id)

    reg_rows = {r.server_id: r for r in db.execute(reg_q).scalars().all()}
    candidate_ids = sorted(reg_rows.keys())

    if not candidate_ids:
        return _header_csv()

    # Step 2: sub-query for latest model_version per server
    latest_mv = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.model_version,
        )
        .where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.server_id.in_(candidate_ids),
        )
        .subquery()
    )

    # Step 3: fetch axis scores for the latest model version
    axis_q = (
        select(McpLlmAxisScore)
        .join(
            latest_mv,
            (McpLlmAxisScore.server_id == latest_mv.c.server_id)
            & (McpLlmAxisScore.model_version == latest_mv.c.model_version),
        )
        .where(McpLlmAxisScore.server_id.in_(candidate_ids))
    )
    axis_rows = db.execute(axis_q).scalars().all()

    if not axis_rows:
        return _header_csv()

    # Step 4: group axis rows by server_id
    axis_by_server: dict = defaultdict(list)
    for r in axis_rows:
        axis_by_server[r.server_id].append(r)

    # Step 5: emit CSV
    def _row_gen():
        yield CSV_HEADERS
        for sid in candidate_ids:
            axis_map = _build_axis_map(axis_by_server.get(sid, []))
            reg = reg_rows.get(sid)
            if reg is None:
                continue

            overall = axis_map.get("overall_risk")
            overall_label = overall.label if overall else ""

            gate = trust_gate(
                reg.url,
                reg.name,
                {"overall_risk": overall_label} if overall_label else {},
            )
            published_risk = gate.get("published_overall_risk", overall_label)

            row = [sid, reg.name or "", reg.risk_tier or "", published_risk]
            for ax in AXES[1:]:
                a = axis_map.get(ax)
                row.append(a.label if a else "")
                row.append(a.p_top if a else "")
            yield row

    buf = io.StringIO()
    w = csv.writer(buf)
    for row in _row_gen():
        w.writerow(row)

    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=verdicts.csv"},
    )


if __name__ == "__main__":  # CI-safe self-test
    import sys as _sys
    _sys.path.insert(0, str(__file__).rsplit("/app/", 1)[0])  # repo root for `app.*` imports
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    s = SessionLocal()
    s.add(McpServerRegistry(server_id="srv1", name="Test Server", risk_tier="high", url="https://example.com"))
    s.add(McpServerRegistry(server_id="srv2", name="Other Server", risk_tier="low", url="https://github.com/microsoft"))
    s.add(McpServerRegistry(server_id="srv3", name="Edge Server", risk_tier="medium", url="https://example.org"))
    s.commit()

    MV = "v3.0_40974559"
    for i, (ax, lbl, pt) in enumerate(
        [
            ("overall_risk", "HIGH", 0.82),
            ("auth_strength", "STRONG", 0.75),
            ("capability_breadth", "BROAD", 0.80),
            ("data_sensitivity", "CRITICAL", 0.88),
            ("network_egress", "EXTERNAL", 0.72),
            ("maintainer_trust", "ESTABLISHED", 0.91),
            ("exploit_surface", "MODERATE", 0.65),
        ],
        start=1,
    ):
        s.add(McpLlmAxisScore(id=i, server_id="srv1", axis_name=ax, label=lbl, p_top=pt, model_version=MV))

    for i, (ax, lbl, pt) in enumerate(
        [
            ("overall_risk", "LOW", 0.21),
            ("auth_strength", "STRONG", 0.92),
            ("capability_breadth", "NARROW", 0.18),
            ("data_sensitivity", "LOW", 0.22),
            ("network_egress", "INTERNAL", 0.15),
            ("maintainer_trust", "VERIFIED", 0.95),
            ("exploit_surface", "MINIMAL", 0.10),
        ],
        start=100,
    ):
        s.add(McpLlmAxisScore(id=i, server_id="srv3", axis_name=ax, label=lbl, p_top=pt, model_version=MV))

    # srv2 has NO axis scores (edge case: scored servers vs unscored)
    s.commit()
    s.close()

    def _override_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_session

    client = TestClient(app)

    # Happy path: no filters -> 1 header + 2 data rows (srv1, srv3)
    resp = client.get("/api/verdict/export")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.headers["content-type"].startswith("text/csv"), \
        f"Expected text/csv, got {resp.headers.get('content-type')}"
    assert "attachment" in resp.headers.get("content-disposition", ""), \
        f"Missing Content-Disposition: {resp.headers}"
    lines = resp.text.strip().splitlines()
    assert len(lines) >= 2, f"Expected header + >=1 data row, got {len(lines)} lines"
    assert lines[0] == ",".join(CSV_HEADERS), f"Header mismatch: {lines[0]!r}"

    # server_id filter -> exactly srv1
    resp2 = client.get("/api/verdict/export?server_id=srv1")
    assert resp2.status_code == 200
    lines2 = resp2.text.strip().splitlines()
    assert len(lines2) == 2, f"Expected 2 lines for srv1-only, got {len(lines2)}"
    assert "srv1" in lines2[1]

    # risk_tier=low -> srv2 (no scores), srv3 (scored as MEDIUM after trust_gate caps HIGH)
    resp3 = client.get("/api/verdict/export?risk_tier=low")
    assert resp3.status_code == 200
    lines3 = resp3.text.strip().splitlines()
    # srv2 has no axis rows so it's skipped; srv3 risk_tier is "medium" not "low"
    assert len(lines3) == 1, f"Expected header-only for low tier (no scored low servers), got {len(lines3)}"

    # risk_tier=medium -> srv3
    resp4 = client.get("/api/verdict/export?risk_tier=medium")
    assert resp4.status_code == 200
    lines4 = resp4.text.strip().splitlines()
    assert len(lines4) == 2, f"Expected 2 lines for medium tier, got {len(lines4)}"
    assert "srv3" in lines4[1]

    # risk_tier=high -> srv1
    resp5 = client.get("/api/verdict/export?risk_tier=high")
    assert resp5.status_code == 200
    lines5 = resp5.text.strip().splitlines()
    assert len(lines5) == 2, f"Expected 2 lines for high tier, got {len(lines5)}"
    assert "srv1" in lines5[1]

    # 400 for unsupported format
    resp6 = client.get("/api/verdict/export?format=json")
    assert resp6.status_code == 400, f"Expected 400 for format=json, got {resp6.status_code}"

    # Empty result for unknown server_id
    resp7 = client.get("/api/verdict/export?server_id=nonexistent")
    assert resp7.status_code == 200
    lines7 = resp7.text.strip().splitlines()
    assert len(lines7) == 1, f"Expected header-only for unknown server, got {len(lines7)}"

    print("PASS")
