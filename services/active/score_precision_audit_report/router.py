# deps: fastapi, pydantic, sqlalchemy
"""score_precision_audit_report — precision audit for LLM axis scores.

GET /api/score-precision-audit-report   Precision metrics per axis and overall.

Auth: public.
Data: app tier via get_session + McpLlmAxisScore + McpServerRegistry.
"""
from __future__ import annotations

from collections import defaultdict
from math import log2
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["score_precision_audit_report"])


# --------------------------------------------------------------------------- #
# Pydantic response shapes
# --------------------------------------------------------------------------- #

class AxisPrecisionMetric(BaseModel):
    axis_name: str
    scored_count: int = Field(..., description="Number of scored records for this axis")
    distinct_labels: int = Field(..., description="Number of distinct label_index values")
    entropy: float = Field(..., description="Entropy of label distribution")
    mean_p_top: float = Field(..., description="Mean p_top value")
    cv_p_top: float = Field(..., description="Coefficient of variation of p_top")
    critical_rate: float = Field(..., description="Fraction of records with escalated=True")
    danger_rate: float = Field(..., description="Fraction of records where p_danger > 0.5")
    model_version_count: int = Field(..., description="Distinct model versions for this axis")


class PrecisionAuditReportResponse(BaseModel):
    axes: list[AxisPrecisionMetric]
    overall_servers: int = Field(..., description="Total unique servers scored")
    total_records: int = Field(..., description="Total axis score records")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _entropy(counts: list[int]) -> float:
    total = sum(counts)
    if total == 0:
        return 0.0
    return -sum((c / total) * log2(c / total) for c in counts if c > 0)


def _cv(values: list[float]) -> float:
    n = len(values)
    if n == 0:
        return 0.0
    mean = sum(values) / n
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / n
    return (var ** 0.5) / mean


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/score-precision-audit-report", response_model=PrecisionAuditReportResponse)
def get_precision_audit_report(
    db: Session = Depends(get_session),
    axis_name: str | None = Query(None, description="Filter to a specific axis"),
    min_scored_at: str | None = Query(None, description="ISO-8600 min scored_at filter"),
) -> PrecisionAuditReportResponse:
    """
    Per-axis precision audit: label entropy, p_top CV, critical/danger rates,
    and model-version spread. Overall totals are also returned.
    """
    # Base query joining scores to registry
    q = db.query(
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.label_index,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_danger,
        McpLlmAxisScore.escalated,
        McpLlmAxisScore.model_version,
        McpLlmAxisScore.server_id,
    ).join(
        McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id
    )

    if axis_name:
        q = q.filter(McpLlmAxisScore.axis_name == axis_name)

    rows = q.all()

    # Organise by axis
    axis_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_servers: set[str] = set()

    for (ax, lidx, p_top, p_dng, esc, mv, sid) in rows:
        axis_rows[ax].append({
            "label_index": lidx,
            "p_top": p_top,
            "p_danger": p_dng,
            "escalated": esc,
            "model_version": mv,
            "server_id": sid,
        })
        all_servers.add(sid)

    axes_out: list[AxisPrecisionMetric] = []

    for ax, recs in axis_rows.items():
        label_counts: dict[int, int] = defaultdict(int)
        p_top_vals: list[float] = []
        versions: set[str] = set()
        crit_count = 0
        dang_count = 0

        for r in recs:
            label_counts[r["label_index"] or 0] += 1
            p_top_vals.append(float(r["p_top"] or 0.0))
            versions.add(r["model_version"] or "")
            if r["escalated"]:
                crit_count += 1
            if (r["p_danger"] or 0) > 0.5:
                dang_count += 1

        n = len(recs)
        axes_out.append(AxisPrecisionMetric(
            axis_name=ax,
            scored_count=n,
            distinct_labels=len(label_counts),
            entropy=_entropy(list(label_counts.values())),
            mean_p_top=sum(p_top_vals) / n if n else 0.0,
            cv_p_top=_cv(p_top_vals),
            critical_rate=crit_count / n if n else 0.0,
            danger_rate=dang_count / n if n else 0.0,
            model_version_count=len(versions),
        ))

    # Sort axes deterministically
    axes_out.sort(key=lambda x: x.axis_name)

    total_records = sum(a.scored_count for a in axes_out)

    return PrecisionAuditReportResponse(
        axes=axes_out,
        overall_servers=len(all_servers),
        total_records=total_records,
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from datetime import datetime
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    # Seed test data
    with TestSessionLocal() as db:
        servers = [
            McpServerRegistry(server_id="srv-001", name="Server Alpha", registry_source="test",
                              url="http://example.com/a", description="A",
                              confidence=0.9, first_seen=datetime.utcnow(),
                              last_seen=datetime.utcnow(), last_scanned=datetime.utcnow(),
                              last_assessed=datetime.utcnow(), meta="{}",
                              scan_count=1, trust_score=0.8, verdict="clean",
                              verdict_reasoning="none", risk_tier="low"),
            McpServerRegistry(server_id="srv-002", name="Server Beta", registry_source="test",
                              url="http://example.com/b", description="B",
                              confidence=0.7, first_seen=datetime.utcnow(),
                              last_seen=datetime.utcnow(), last_scanned=datetime.utcnow(),
                              last_assessed=datetime.utcnow(), meta="{}",
                              scan_count=1, trust_score=0.6, verdict="clean",
                              verdict_reasoning="none", risk_tier="medium"),
            McpServerRegistry(server_id="srv-003", name="Server Gamma", registry_source="test",
                              url="http://example.com/c", description="C",
                              confidence=0.8, first_seen=datetime.utcnow(),
                              last_seen=datetime.utcnow(), last_scanned=datetime.utcnow(),
                              last_assessed=datetime.utcnow(), meta="{}",
                              scan_count=1, trust_score=0.9, verdict="clean",
                              verdict_reasoning="none", risk_tier="low"),
        ]
        db.add_all(servers)
        db.flush()

        scores = [
            # axis: overall_risk
            McpLlmAxisScore(server_id="srv-001", axis_name="overall_risk", label_index=0,
                            p_top=0.90, p_critical=0.05, p_danger=0.05, escalated=False,
                            model_version="v1"),
            McpLlmAxisScore(server_id="srv-001", axis_name="overall_risk", label_index=0,
                            p_top=0.88, p_critical=0.07, p_danger=0.05, escalated=False,
                            model_version="v2"),
            McpLlmAxisScore(server_id="srv-002", axis_name="overall_risk", label_index=1,
                            p_top=0.65, p_critical=0.20, p_danger=0.15, escalated=True,
                            model_version="v1"),
            McpLlmAxisScore(server_id="srv-002", axis_name="overall_risk", label_index=2,
                            p_top=0.55, p_critical=0.25, p_danger=0.20, escalated=True,
                            model_version="v2"),
            McpLlmAxisScore(server_id="srv-003", axis_name="overall_risk", label_index=0,
                            p_top=0.95, p_critical=0.03, p_danger=0.02, escalated=False,
                            model_version="v1"),
            # axis: auth_strength
            McpLlmAxisScore(server_id="srv-001", axis_name="auth_strength", label_index=0,
                            p_top=0.80, p_critical=0.10, p_danger=0.10, escalated=False,
                            model_version="v1"),
            McpLlmAxisScore(server_id="srv-002", axis_name="auth_strength", label_index=1,
                            p_top=0.60, p_critical=0.25, p_danger=0.15, escalated=False,
                            model_version="v1"),
            McpLlmAxisScore(server_id="srv-003", axis_name="auth_strength", label_index=0,
                            p_top=0.85, p_critical=0.08, p_danger=0.07, escalated=False,
                            model_version="v1"),
        ]
        db.add_all(scores)
        db.commit()

    client = TestClient(app)

    # 1. Happy path – full report
    r = client.get("/api/score-precision-audit-report")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    body = r.json()
    assert "axes" in body, "Missing 'axes' key"
    assert "overall_servers" in body
    assert "total_records" in body
    assert len(body["axes"]) == 2, f"Expected 2 axes, got {len(body['axes'])}"
    assert body["overall_servers"] == 3, f"Expected 3 servers, got {body['overall_servers']}"
    axis_names = {a["axis_name"] for a in body["axes"]}
    assert "overall_risk" in axis_names
    assert "auth_strength" in axis_names

    # 2. Filter by axis
    r = client.get("/api/score-precision-audit-report?axis_name=overall_risk")
    assert r.status_code == 200
    body2 = r.json()
    assert len(body2["axes"]) == 1
    assert body2["axes"][0]["axis_name"] == "overall_risk"

    # 3. p_top/cv are floats
    for ax in body["axes"]:
        assert isinstance(ax["mean_p_top"], float), f"mean_p_top not float: {ax}"
        assert isinstance(ax["cv_p_top"], float), f"cv_p_top not float: {ax}"
        assert ax["scored_count"] > 0, f"scored_count should be > 0: {ax}"

    # 4. Empty filter returns 200 with empty axes
    r = client.get("/api/score-precision-audit-report?axis_name=nonexistent")
    assert r.status_code == 200
    assert r.json()["axes"] == []

    print("PASS")
    sys.exit(0)
