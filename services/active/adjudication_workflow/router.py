# deps: fastapi, pydantic, sqlalchemy, requests
"""adjudication_workflow -- human-review pipeline for disputed scores.

GET  /api/adjudication/cases
  List adjudication cases, ordered by created_at desc. Optional filters for
  status and priority.

GET  /api/adjudication/cases/{case_id}
  Return a single case with its dispute and server details.

POST /api/adjudication/cases
  Open a new adjudication case from an existing McpScoreDispute.

PATCH /api/adjudication/cases/{case_id}
  Update case status, priority, or analyst notes.

POST /api/adjudication/cases/{case_id}/decide
  Record a final decision (approve, overrule, escalate) and optionally
  update the dispute record.

GET  /api/adjudication/cases/{case_id}/signals
  Fetch correlated signal scores (mesh) + LLM axis scores (app) for the
  case's server_id, aiding the analyst's review.

Auth: public (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + McpScoreDispute + McpServerRegistry +
      McpLlmAxisScore; mesh via write_service :8772/query.
"""
from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

import requests

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

router = APIRouter(prefix="/api", tags=["adjudication_workflow"])

#: write_service base URL for mesh/pipeline table access.
_WRITE_SERVICE = "http://127.0.0.1:8772"
_WRITE_TIMEOUT = 10  # seconds


# --------------------------------------------------------------------------- #
# Pydantic shapes
# --------------------------------------------------------------------------- #

class DecisionBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    verdict: str
    reasoning: str
    override_score: Optional[float] = None
    override_tier: Optional[str] = None
    notes: Optional[str] = None


class DecisionCreate(DecisionBase):
    pass


class DecisionResponse(DecisionBase):
    id: int
    case_id: int
    decided_by: str
    decided_at: datetime


class CaseSignalScore(BaseModel):
    """A single signal-score row from the mesh mcp_signal_scores table."""
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    perspective_name: str
    signal_name: str
    score_value: Optional[float] = None
    score_label: Optional[str] = None
    confidence: Optional[float] = None
    computed_at: Optional[datetime] = None


class CaseAxisScore(BaseModel):
    """A single LLM axis-score row from the app mcp_llm_axis_scores table."""
    model_config = ConfigDict(from_attributes=True)

    axis_name: str
    label: str
    label_index: int
    probs: Optional[dict] = None
    p_top: Optional[float] = None
    p_critical: Optional[float] = None
    p_danger: Optional[float] = None
    escalated: bool
    escalated_to: Optional[str] = None
    model_version: Optional[str] = None
    scored_at: datetime


class CaseDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    case_id: int
    dispute_id: int
    server_id: str
    server_name: Optional[str] = None
    status: str
    priority: str
    analyst_name: Optional[str] = None
    analyst_notes: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    dispute: Optional[dict] = None
    decision: Optional[DecisionResponse] = None


class CaseCreate(BaseModel):
    dispute_id: int
    priority: str = "normal"
    analyst_name: Optional[str] = None
    analyst_notes: Optional[str] = None


class CaseUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    analyst_name: Optional[str] = None
    analyst_notes: Optional[str] = None


class CaseListResponse(BaseModel):
    items: list[CaseDetail]
    total: int
    limit: int
    offset: int


class SignalsResponse(BaseModel):
    server_id: str
    mesh_signals: list[CaseSignalScore]
    axis_scores: list[CaseAxisScore]
    signal_count: int
    axis_count: int


# --------------------------------------------------------------------------- #
# In-memory case store (workflow state; not persisted to DB by this module)
# --------------------------------------------------------------------------- #
# Adjudication cases are stored in-memory keyed by case_id.
# Decisions are stored in-memory keyed by case_id.
# In a production deployment this would be a dedicated table or a job-queue.
# The in-memory store is safe for the __main__ self-test and for a single-
# process FastAPI instance that does not fork.

_cases: Dict[int, Dict[str, Any]] = {}
_decisions: Dict[int, Dict[str, Any]] = {}
_next_case_id = 1
_cases_lock = __import__("threading").Lock()


def _gen_case_id() -> int:
    global _next_case_id
    with _cases_lock:
        cid = _next_case_id
        _next_case_id += 1
        return cid


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _fetch_mesh_signals(server_id: str) -> list[CaseSignalScore]:
    """Query mcp_signal_scores from the mesh via write_service."""
    try:
        resp = requests.post(
            f"{_WRITE_SERVICE}/query",
            json={"sql": "SELECT server_id, perspective_name, signal_name, score_value, score_label, confidence, computed_at FROM mcp_signal_scores WHERE server_id = ?", "params": [server_id]},
            timeout=_WRITE_TIMEOUT,
        )
        if resp.status_code == 200:
            rows = resp.json()
            if isinstance(rows, list):
                result = []
                for row in rows:
                    computed_at = None
                    if row.get("computed_at"):
                        try:
                            computed_at = datetime.fromisoformat(row["computed_at"].replace("Z", "+00:00"))
                        except Exception:
                            pass
                    result.append(CaseSignalScore(
                        server_id=row.get("server_id", server_id),
                        perspective_name=row.get("perspective_name", ""),
                        signal_name=row.get("signal_name", ""),
                        score_value=row.get("score_value"),
                        score_label=row.get("score_label"),
                        confidence=row.get("confidence"),
                        computed_at=computed_at,
                    ))
                return result
        return []
    except Exception:
        return []


def _fetch_axis_scores(db: Session, server_id: str) -> list[CaseAxisScore]:
    """Query mcp_llm_axis_scores from the app tier."""
    rows = db.execute(
        select(McpLlmAxisScore)
        .where(McpLlmAxisScore.server_id == server_id)
        .order_by(desc(McpLlmAxisScore.scored_at))
    ).scalars().all()
    return [
        CaseAxisScore(
            axis_name=r.axis_name,
            label=r.label,
            label_index=r.label_index,
            probs=_json.loads(r.probs) if r.probs else None,
            p_top=r.p_top,
            p_critical=r.p_critical,
            p_danger=r.p_danger,
            escalated=r.escalated,
            escalated_to=r.escalated_to,
            model_version=r.model_version,
            scored_at=r.scored_at,
        )
        for r in rows
    ]


def _dispute_dict(dispute: McpScoreDispute) -> dict:
    return {
        "id": dispute.id,
        "server_id": dispute.server_id,
        "submitted_by": dispute.submitted_by,
        "proposed_overall_risk": dispute.proposed_overall_risk,
        "proposed_axes": _json.loads(dispute.proposed_axes) if dispute.proposed_axes else None,
        "reason_category": dispute.reason_category,
        "explanation": dispute.explanation,
        "status": dispute.status,
        "admin_note": dispute.admin_note,
        "created_at": dispute.created_at.isoformat() if dispute.created_at else None,
        "resolved_at": dispute.resolved_at.isoformat() if dispute.resolved_at else None,
    }


def _build_case_detail(case: Dict[str, Any], dispute: Optional[McpScoreDispute]) -> CaseDetail:
    server_name = None
    if dispute:
        # Resolve server name from registry
        pass  # resolved in endpoint
    decision = None
    if case.get("decision"):
        d = case["decision"]
        decision = DecisionResponse(
            id=d["id"],
            case_id=d["case_id"],
            verdict=d["verdict"],
            reasoning=d["reasoning"],
            override_score=d.get("override_score"),
            override_tier=d.get("override_tier"),
            notes=d.get("notes"),
            decided_by=d["decided_by"],
            decided_at=d["decided_at"],
        )
    return CaseDetail(
        case_id=case["case_id"],
        dispute_id=case["dispute_id"],
        server_id=case["server_id"],
        server_name=case.get("server_name"),
        status=case["status"],
        priority=case["priority"],
        analyst_name=case.get("analyst_name"),
        analyst_notes=case.get("analyst_notes"),
        created_at=case["created_at"],
        updated_at=case.get("updated_at"),
        resolved_at=case.get("resolved_at"),
        dispute=_dispute_dict(dispute) if dispute else None,
        decision=decision,
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

@router.get("/adjudication/cases", response_model=CaseListResponse)
def list_cases(
    db: Session = Depends(get_session),
    case_status: Optional[str] = Query(None, alias="status", description="Filter by case status"),
    priority: Optional[str] = Query(None, description="Filter by priority"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> CaseListResponse:
    """List all adjudication cases, newest first. Filters: status, priority."""
    with _cases_lock:
        all_cases = list(_cases.values())

    if case_status:
        all_cases = [c for c in all_cases if c["status"] == case_status]
    if priority:
        all_cases = [c for c in all_cases if c["priority"] == priority]

    all_cases.sort(key=lambda c: c["created_at"], reverse=True)
    total = len(all_cases)
    page = all_cases[offset : offset + limit]

    items = []
    for case in page:
        dispute = None
        if case.get("dispute_id"):
            dispute = db.execute(
                select(McpScoreDispute).where(McpScoreDispute.id == case["dispute_id"])
            ).scalar_one_or_none()
        items.append(_build_case_detail(case, dispute))

    return CaseListResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/adjudication/cases/{case_id}", response_model=CaseDetail)
def get_case(case_id: int, db: Session = Depends(get_session)) -> CaseDetail:
    """Return a single adjudication case with its dispute, or 404."""
    with _cases_lock:
        case = _cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")

    dispute = None
    if case.get("dispute_id"):
        dispute = db.execute(
            select(McpScoreDispute).where(McpScoreDispute.id == case["dispute_id"])
        ).scalar_one_or_none()

    # Resolve server name
    if dispute:
        server_name = db.execute(
            select(McpServerRegistry.name).where(McpServerRegistry.server_id == dispute.server_id)
        ).scalar_one_or_none()
        if server_name:
            case["server_name"] = server_name

    return _build_case_detail(case, dispute)


@router.post("/adjudication/cases", response_model=CaseDetail, status_code=status.HTTP_201_CREATED)
def create_case(data: CaseCreate, db: Session = Depends(get_session)) -> CaseDetail:
    """Open a new adjudication case from an existing McpScoreDispute."""
    # Verify dispute exists
    dispute = db.execute(
        select(McpScoreDispute).where(McpScoreDispute.id == data.dispute_id)
    ).scalar_one_or_none()
    if dispute is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dispute not found")

    server_name = db.execute(
        select(McpServerRegistry.name).where(McpServerRegistry.server_id == dispute.server_id)
    ).scalar_one_or_none()

    now = _utcnow()
    case_id = _gen_case_id()
    case = {
        "case_id": case_id,
        "dispute_id": data.dispute_id,
        "server_id": dispute.server_id,
        "server_name": server_name,
        "status": "pending",
        "priority": data.priority,
        "analyst_name": data.analyst_name,
        "analyst_notes": data.analyst_notes,
        "created_at": now,
        "updated_at": now,
        "resolved_at": None,
        "decision": None,
    }
    with _cases_lock:
        _cases[case_id] = case

    return _build_case_detail(case, dispute)


@router.patch("/adjudication/cases/{case_id}", response_model=CaseDetail)
def update_case(case_id: int, data: CaseUpdate, db: Session = Depends(get_session)) -> CaseDetail:
    """Update case status, priority, analyst, or notes."""
    with _cases_lock:
        if case_id not in _cases:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
        case = _cases[case_id]

    dispute = None
    if case.get("dispute_id"):
        dispute = db.execute(
            select(McpScoreDispute).where(McpScoreDispute.id == case["dispute_id"])
        ).scalar_one_or_none()

    now = _utcnow()
    updated = False
    if data.status is not None:
        case["status"] = data.status
        updated = True
    if data.priority is not None:
        case["priority"] = data.priority
        updated = True
    if data.analyst_name is not None:
        case["analyst_name"] = data.analyst_name
        updated = True
    if data.analyst_notes is not None:
        case["analyst_notes"] = data.analyst_notes
        updated = True

    if updated:
        case["updated_at"] = now
        if data.status == "resolved" and case.get("resolved_at") is None:
            case["resolved_at"] = now
        with _cases_lock:
            _cases[case_id] = case

    return _build_case_detail(case, dispute)


@router.post("/adjudication/cases/{case_id}/decide", response_model=DecisionResponse)
def decide_case(
    case_id: int,
    data: DecisionCreate,
    db: Session = Depends(get_session),
) -> DecisionResponse:
    """Record a final adjudication decision (approve / overrule / escalate) and
    optionally update the linked dispute record."""
    with _cases_lock:
        if case_id not in _cases:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
        case = _cases[case_id]

    now = _utcnow()

    decision_id = len(_decisions) + 1
    decision = {
        "id": decision_id,
        "case_id": case_id,
        "verdict": data.verdict,
        "reasoning": data.reasoning,
        "override_score": data.override_score,
        "override_tier": data.override_tier,
        "notes": data.notes,
        "decided_by": "system",
        "decided_at": now,
    }
    with _cases_lock:
        _decisions[case_id] = decision
        case["decision"] = decision
        case["status"] = "resolved"
        case["resolved_at"] = now
        case["updated_at"] = now
        _cases[case_id] = case

    # Update linked dispute record
    if case.get("dispute_id"):
        dispute = db.execute(
            select(McpScoreDispute).where(McpScoreDispute.id == case["dispute_id"])
        ).scalar_one_or_none()
        if dispute:
            dispute.status = "resolved"
            dispute.admin_note = f"[{data.verdict}] {data.reasoning}"
            if data.notes:
                dispute.admin_note += f"\n{data.notes}"
            dispute.resolved_at = now
            db.commit()

    return DecisionResponse(
        id=decision["id"],
        case_id=decision["case_id"],
        verdict=decision["verdict"],
        reasoning=decision["reasoning"],
        override_score=decision.get("override_score"),
        override_tier=decision.get("override_tier"),
        notes=decision.get("notes"),
        decided_by=decision["decided_by"],
        decided_at=decision["decided_at"],
    )


@router.get("/adjudication/cases/{case_id}/signals", response_model=SignalsResponse)
def get_case_signals(case_id: int, db: Session = Depends(get_session)) -> SignalsResponse:
    """Fetch correlated signal scores (mesh) and LLM axis scores (app) for the
    case's server_id, aiding the analyst's review."""
    with _cases_lock:
        if case_id not in _cases:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found")
        case = _cases[case_id]

    server_id = case["server_id"]

    mesh_signals = _fetch_mesh_signals(server_id)
    axis_scores = _fetch_axis_scores(db, server_id)

    return SignalsResponse(
        server_id=server_id,
        mesh_signals=mesh_signals,
        axis_scores=axis_scores,
        signal_count=len(mesh_signals),
        axis_count=len(axis_scores),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.pool import StaticPool

    Base = declarative_base()

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                server_id VARCHAR(128) PRIMARY KEY,
                name VARCHAR(256)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_score_disputes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(128) NOT NULL,
                submitted_by VARCHAR(128) NOT NULL,
                proposed_overall_risk VARCHAR(16),
                proposed_axes TEXT,
                reason_category VARCHAR(48),
                explanation TEXT,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                admin_note TEXT,
                created_at TIMESTAMP NOT NULL,
                resolved_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(128) NOT NULL,
                axis_name VARCHAR(64) NOT NULL,
                label VARCHAR(32) NOT NULL,
                label_index INTEGER NOT NULL,
                probs TEXT,
                p_top REAL,
                p_critical REAL,
                p_danger REAL,
                escalated INTEGER NOT NULL DEFAULT 0,
                escalated_to VARCHAR(32),
                decision_rule_version VARCHAR(32),
                model_version VARCHAR(32),
                adapter_sha256 VARCHAR(64),
                scored_at TIMESTAMP NOT NULL
            )
        """))
        conn.commit()

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override():
        sess = SessionLocal()
        try:
            yield sess
        finally:
            sess.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    from datetime import timedelta
    now = datetime.now(timezone.utc)

    with SessionLocal() as sess:
        sess.execute(
            text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:sid, :name)"),
            {"sid": "srv_x", "name": "Server X"},
        )
        sess.execute(
            text("INSERT INTO mcp_score_disputes (server_id, submitted_by, proposed_overall_risk, reason_category, explanation, status, created_at) VALUES (:sid, :sb, :risk, :cat, :exp, 'pending', :ca)"),
            {"sid": "srv_x", "sb": "user_a", "risk": "HIGH", "cat": "incorrect_category", "exp": "Score should be HIGH", "ca": now - timedelta(days=2)},
        )
        sess.execute(
            text("INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, label_index, p_top, p_critical, p_danger, escalated, model_version, scored_at) VALUES (:sid, :ax, :lbl, :idx, :pt, :pc, :pd, 0, 'v1', :sa)"),
            {"sid": "srv_x", "ax": "overall_risk", "lbl": "MEDIUM", "idx": 1, "pt": 0.55, "pc": 0.1, "pd": 0.2, "sa": now - timedelta(days=1)},
        )
        sess.commit()

    client = TestClient(app)

    # POST create case from dispute 1
    r = client.post("/api/adjudication/cases", json={
        "dispute_id": 1,
        "priority": "high",
        "analyst_name": "analyst_1",
    })
    assert r.status_code == 201, r.text
    case = r.json()
    assert case["status"] == "pending"
    assert case["priority"] == "high"
    assert case["server_id"] == "srv_x"
    assert case["server_name"] == "Server X"
    assert case["dispute"]["id"] == 1
    case_id = case["case_id"]

    # GET case detail
    r = client.get(f"/api/adjudication/cases/{case_id}")
    assert r.status_code == 200, r.text
    assert r.json()["case_id"] == case_id

    # GET list (should have 1 case)
    r = client.get("/api/adjudication/cases")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 1, f"total={data['total']}"

    # PATCH update status
    r = client.patch(f"/api/adjudication/cases/{case_id}", json={"status": "in_review", "analyst_notes": "Under review"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_review"
    assert r.json()["analyst_notes"] == "Under review"

    # POST decision
    r = client.post(f"/api/adjudication/cases/{case_id}/decide", json={
        "verdict": "overrule",
        "reasoning": "Axis scores confirm MEDIUM",
        "notes": "No change warranted",
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["verdict"] == "overrule"
    assert d["case_id"] == case_id

    # GET case after decision (status should be resolved)
    r = client.get(f"/api/adjudication/cases/{case_id}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "resolved"
    assert r.json()["decision"]["verdict"] == "overrule"

    # GET signals (axis scores from app + mesh signals from mock)
    r = client.get(f"/api/adjudication/cases/{case_id}/signals")
    assert r.status_code == 200, r.text
    sig = r.json()
    assert sig["server_id"] == "srv_x"
    assert sig["axis_count"] == 1, f"axis_count={sig['axis_count']}"
    assert sig["mesh_signals"] == []  # no mesh data in self-test

    # GET signals for unknown case
    r = client.get("/api/adjudication/cases/9999/signals")
    assert r.status_code == 404

    # POST decide unknown case
    r = client.post("/api/adjudication/cases/9999/decide", json={"verdict": "approve", "reasoning": "x"})
    assert r.status_code == 404

    # POST case with unknown dispute
    r = client.post("/api/adjudication/cases", json={"dispute_id": 9999})
    assert r.status_code == 404

    print("PASS")
