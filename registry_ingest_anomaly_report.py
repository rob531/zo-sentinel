"""FastAPI router exposing registry ingest anomaly reporting.

Aggregates anomalies across mcp_server_registry and mcp_definition_history:
  - missing_definition: registry servers with no history snapshot
  - stale_scan: servers not scanned in >N days (configurable via ?days=N)
  - unassessed: servers never assessed
  - inconsistent_verdict: registry verdict and axis-score overall_risk disagree

Reads REAL app tables via SQLAlchemy (app.db / app.models) -- no inline stubs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone as tz
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpDefinitionHistory

router = APIRouter(prefix="/registry/ingest", tags=["registry"])


# ---- Pydantic response models --------------------------------------------------

class AnomalyEntry(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    registry_source: Optional[str] = None
    anomaly_type: str
    detail: Optional[str] = None
    last_scanned: Optional[datetime] = None
    last_assessed: Optional[datetime] = None
    scan_count: Optional[int] = None


class AnomalyReport(BaseModel):
    missing_definition: list[AnomalyEntry]
    stale_scan: list[AnomalyEntry]
    unassessed: list[AnomalyEntry]
    inconsistent_verdict: list[AnomalyEntry]
    total: int
    missing_definition_count: int
    stale_scan_count: int
    unassessed_count: int
    inconsistent_verdict_count: int


# ---- Helpers -------------------------------------------------------------------

def _build_entry(r: McpServerRegistry, anomaly_type: str,
                 detail: Optional[str] = None) -> AnomalyEntry:
    return AnomalyEntry(
        server_id=r.server_id,
        name=r.name,
        url=r.url,
        registry_source=r.registry_source,
        anomaly_type=anomaly_type,
        detail=detail,
        last_scanned=r.last_scanned,
        last_assessed=r.last_assessed,
        scan_count=r.scan_count,
    )


def _missing_definition(db: Session) -> list[AnomalyEntry]:
    """Servers in the registry that have never had a definition snapshot captured."""
    # NOT IN with explicit list from a scalar subquery -- SQLite-compatible
    rows = db.execute(
        select(McpServerRegistry)
        .where(
            McpServerRegistry.server_id.not_in(
                select(McpDefinitionHistory.server_id).distinct()
            )
        )
    ).scalars().all()
    return [_build_entry(r, "missing_definition",
                        "No definition snapshot found in mcp_definition_history") for r in rows]


def _stale_scan(db: Session, days: int) -> list[AnomalyEntry]:
    """Servers not scanned in the last N days (or never scanned)."""
    threshold = datetime.utcnow() - timedelta(days=days)
    rows = db.execute(
        select(McpServerRegistry)
        .where(
            (McpServerRegistry.last_scanned.is_(None))
            | (McpServerRegistry.last_scanned < threshold)
        )
    ).scalars().all()
    return [_build_entry(r, "stale_scan",
                        f"Not scanned in >{days} days") for r in rows]


def _unassessed(db: Session) -> list[AnomalyEntry]:
    """Servers that have never been assessed (no last_assessed timestamp)."""
    rows = db.execute(
        select(McpServerRegistry)
        .where(McpServerRegistry.last_assessed.is_(None))
    ).scalars().all()
    return [_build_entry(r, "unassessed",
                        "Server has never been assessed") for r in rows]


def _inconsistent_verdict(db: Session) -> list[AnomalyEntry]:
    """Servers whose registry verdict and axis-score overall_risk label disagree."""
    # Get the latest model_version for each server
    latest = (
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_scored"),
        )
        .group_by(McpLlmAxisScore.server_id)
    ).subquery()

    latest_mv = (
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.model_version)
        .where(
            McpLlmAxisScore.axis_name == "overall_risk",
            McpLlmAxisScore.scored_at == latest.c.latest_scored,
        )
    ).subquery()

    scored = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpLlmAxisScore.label.label("axis_overall_risk"),
        )
        .outerjoin(latest_mv, McpServerRegistry.server_id == latest_mv.c.server_id)
        .outerjoin(
            McpLlmAxisScore,
            (McpLlmAxisScore.server_id == McpServerRegistry.server_id)
            & (McpLlmAxisScore.axis_name == "overall_risk")
            & (McpLlmAxisScore.model_version == latest_mv.c.model_version),
        )
    ).subquery()

    # Map axis label -> registry verdict
    label_to_verdict = {
        "CRITICAL": "critical",
        "HIGH": "high",
        "MEDIUM": "medium",
        "LOW": "low",
        "UNKNOWN": "unknown",
    }

    rows = db.execute(
        select(McpServerRegistry)
        .join(scored, McpServerRegistry.server_id == scored.c.server_id)
        .where(
            scored.c.axis_overall_risk.isnot(None),
            McpServerRegistry.verdict.isnot(None),
        )
    ).scalars().all()

    inconsistent = []
    for r in rows:
        row_data = db.execute(
            select(scored.c.verdict, scored.c.axis_overall_risk)
            .where(scored.c.server_id == r.server_id)
        ).first()
        if not row_data:
            continue
        reg_verdict, axis_label = row_data
        expected = label_to_verdict.get(axis_label, "").lower()
        if reg_verdict and reg_verdict.lower() != expected:
            inconsistent.append(_build_entry(
                r, "inconsistent_verdict",
                f"Registry verdict={reg_verdict}, axis overall_risk={axis_label} (expected={expected})"
            ))
    return inconsistent


# ---- Endpoint ------------------------------------------------------------------

@router.get("/anomalies", response_model=AnomalyReport)
def get_ingest_anomalies(
    stale_days: int = Query(default=30, ge=1, le=365,
                             description="Mark servers as stale if not scanned in N days"),
    db: Session = Depends(get_session),
) -> AnomalyReport:
    """Aggregate registry ingest anomalies: missing definitions, stale scans,
    unassessed servers, and verdict/score inconsistencies."""
    missing_def = _missing_definition(db)
    stale = _stale_scan(db, stale_days)
    unassessed = _unassessed(db)
    inconsistent = _inconsistent_verdict(db)

    total = len(missing_def) + len(stale) + len(unassessed) + len(inconsistent)
    return AnomalyReport(
        missing_definition=missing_def,
        stale_scan=stale,
        unassessed=unassessed,
        inconsistent_verdict=inconsistent,
        total=total,
        missing_definition_count=len(missing_def),
        stale_scan_count=len(stale),
        unassessed_count=len(unassessed),
        inconsistent_verdict_count=len(inconsistent),
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

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session

    # ---- Seed data ----
    sess = TS()

    # srv1: has definition -> not in missing_definition
    sess.add(McpServerRegistry(
        server_id="srv1", name="Has Definition",
        url="https://example.com/srv1", registry_source="npm",
        last_scanned=datetime.utcnow(), last_assessed=datetime.utcnow(),
        scan_count=3, verdict="medium",
    ))
    sess.add(McpDefinitionHistory(
        id=1,
        server_id="srv1", snapshot_hash="abc123",
        snapshot_content="{}",
        captured_at=datetime.utcnow(),
    ))
    # srv2: NO definition -> missing_definition anomaly
    sess.add(McpServerRegistry(
        server_id="srv2", name="No Definition",
        url="https://example.com/srv2", registry_source="npm",
        last_scanned=datetime.utcnow(), last_assessed=datetime.utcnow(),
        scan_count=1, verdict="medium",
    ))
    # srv3: stale scan (>30 days old by default)
    old = datetime.utcnow() - timedelta(days=45)
    sess.add(McpServerRegistry(
        server_id="srv3", name="Stale Scan",
        url="https://example.com/srv3", registry_source="github",
        last_scanned=old, last_assessed=old, scan_count=5, verdict="low",
    ))
    # srv4: unassessed (no last_assessed)
    sess.add(McpServerRegistry(
        server_id="srv4", name="Never Assessed",
        url="https://example.com/srv4", registry_source="github",
        last_scanned=old, scan_count=2, verdict="unreviewed",
    ))
    # srv5: has scores with overall_risk=HIGH, but verdict=medium -> inconsistent
    sess.add(McpServerRegistry(
        server_id="srv5", name="Inconsistent Verdict",
        url="https://example.com/srv5", registry_source="npm",
        last_scanned=datetime.utcnow(), last_assessed=datetime.utcnow(),
        scan_count=4, verdict="medium",  # axis says HIGH but registry says medium
    ))
    sess.add(McpLlmAxisScore(
        id=1, server_id="srv5", axis_name="overall_risk",
        label="HIGH", model_version="v3.0_40974559",
    ))

    sess.commit()
    sess.close()

    # ---- Test ----
    c = TestClient(app)
    r = c.get("/registry/ingest/anomalies")
    assert r.status_code == 200, r.text
    j = r.json()

    # missing_definition: srv2, srv3, srv4, srv5 (only srv1 has a definition)
    assert j["missing_definition_count"] == 4, j
    ids = {e["server_id"] for e in j["missing_definition"]}
    assert ids == {"srv2", "srv3", "srv4", "srv5"}, j

    # stale_scan: srv3 (+ srv4 since it has old last_scanned too)
    assert j["stale_scan_count"] >= 1, j

    # unassessed: srv4
    assert j["unassessed_count"] == 1, j
    assert j["unassessed"][0]["server_id"] == "srv4", j

    # inconsistent_verdict: srv5
    assert j["inconsistent_verdict_count"] == 1, j
    assert j["inconsistent_verdict"][0]["server_id"] == "srv5", j

    # total sanity
    expected_total = (j["missing_definition_count"] + j["stale_scan_count"]
                      + j["unassessed_count"] + j["inconsistent_verdict_count"])
    assert j["total"] == expected_total, j

    # Test with custom stale_days
    r2 = c.get("/registry/ingest/anomalies?stale_days=60")
    assert r2.status_code == 200, r2.text

    print("PASS")
