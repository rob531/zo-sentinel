#!/usr/bin/env python3
"""
approval_workflow.py  -- ZO-SENTINEL Phase 4
FastAPI backend for the InfoSec MCP approval workflow.
Port: 8780

Endpoints:
  POST /api/submit     -- developer submits MCP for InfoSec review
  GET  /api/submission/{id} -- full intelligence brief for analyst
  POST /api/decision/{id}   -- analyst records APPROVED/CONDITIONAL/REJECTED
  GET  /api/registry        -- paginated list of assessed MCPs
  GET  /api/audit           -- audit trail of all decisions
  GET  /health              -- service health

All DB writes via write_service on port 8772 (never direct DuckDB).
"""
import uuid, json, logging, requests, uvicorn
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

log = logging.getLogger(__name__)
WRITE_SERVICE = "http://127.0.0.1:8772"
PORT          = 8780

app = FastAPI(
    title="ZO-SENTINEL Approval Workflow",
    description="InfoSec MCP approval workflow API",
    version="1.0.0"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ── Write service helpers ───────────────────────────────────────────────────────

def ws_write(table: str, row: dict) -> bool:
    try:
        r = requests.post(f"{WRITE_SERVICE}/write",
            json={"table": table, "rows": row, "wait": True}, timeout=10)
        return r.status_code == 200
    except Exception as e:
        log.error(f"ws_write {table}: {e}")
        return False

def ws_query(sql: str, limit: int = 100) -> list:
    try:
        r = requests.post(f"{WRITE_SERVICE}/query",
            json={"sql": sql, "limit": limit}, timeout=10)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.error(f"ws_query: {e}")
    return []

def next_id() -> int:
    """Generate a time-based BIGINT id."""
    import time
    return int(time.time() * 1000) % (2**62)


# ── Request / Response models ────────────────────────────────────────────────────

class SubmitRequest(BaseModel):
    mcp_identifier:   str
    requester_name:   str
    requester_team:   str
    business_purpose: str
    environment:      str  # Production | Staging | Research | Development

class DecisionRequest(BaseModel):
    analyst_name: str
    decision:     str           # APPROVED | CONDITIONAL | REJECTED
    conditions:   Optional[str] = None
    notes:        Optional[str] = None
    expiry_days:  Optional[int] = 90


# ── Intelligence brief helper ──────────────────────────────────────────────────

def get_intelligence_brief(mcp_identifier: str) -> dict:
    """Fetch the current assessment for an MCP identifier."""
    rows = ws_query(
        f"SELECT server_id, name, trust_score, verdict, verdict_reasoning, "
        f"confidence, last_assessed FROM mcp_server_registry "
        f"WHERE name = '{mcp_identifier}' OR url LIKE '%{mcp_identifier}%' "
        f"OR server_id = '{mcp_identifier}' LIMIT 1"
    )
    if not rows:
        return {
            "verdict":          "INSUFFICIENT",
            "trust_score":      None,
            "reasoning":        "No assessment data found for this MCP identifier.",
            "signals":          [],
            "data_freshness":   "unknown",
            "caveats":          ["Submit for manual analysis before approving"]
        }
    rec = rows[0]
    server_id = rec.get("server_id", "")

    # Fetch signal scores
    signals = ws_query(
        f"SELECT signal_name, score, evidence FROM mcp_signal_scores "
        f"WHERE server_id = '{server_id}' ORDER BY scored_at DESC LIMIT 12"
    )

    # Dedup signals (keep most recent per signal_name)
    seen, deduped = set(), []
    for s in signals:
        if s["signal_name"] not in seen:
            seen.add(s["signal_name"])
            deduped.append(s)

    age_str = "unknown"
    if rec.get("last_assessed"):
        try:
            assessed = datetime.fromisoformat(str(rec["last_assessed"]).replace("Z","+00:00"))
            hours = (datetime.now(timezone.utc) - assessed).total_seconds() / 3600
            age_str = f"{hours:.0f}h ago"
        except Exception:
            pass

    return {
        "server_id":      server_id,
        "verdict":        rec.get("verdict", "INSUFFICIENT"),
        "trust_score":    rec.get("trust_score"),
        "reasoning":      rec.get("verdict_reasoning", ""),
        "confidence":     rec.get("confidence"),
        "signals":        deduped,
        "data_freshness": age_str,
        "caveats":        [
            "ZO-SENTINEL provides probabilistic intelligence, not certification.",
            "Always apply independent security review before production deployment."
        ]
    }


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "service": "approval_workflow", "port": PORT}


@app.post("/api/submit")
def submit(req: SubmitRequest):
    """Developer submits an MCP for InfoSec review."""
    submission_id = str(uuid.uuid4())[:8].upper()
    now = datetime.now(timezone.utc).isoformat()

    # Pull existing intelligence brief
    brief = get_intelligence_brief(req.mcp_identifier)

    # Write submission record
    ok = ws_write("mcp_submissions", {
        "id":              next_id(),
        "submission_id":   submission_id,
        "mcp_identifier":  req.mcp_identifier,
        "requester_name":  req.requester_name,
        "requester_team":  req.requester_team,
        "business_purpose": req.business_purpose,
        "environment":     req.environment,
        "submitted_at":    now,
        "status":          "pending"
    })
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to write submission")

    log.info(f"Submission {submission_id}: {req.mcp_identifier} from {req.requester_team}")
    return {
        "submission_id":   submission_id,
        "status":          "pending",
        "initial_verdict": brief["verdict"],
        "trust_score":     brief["trust_score"],
        "message":         "Submitted for InfoSec review. Track at /api/submission/" + submission_id
    }


@app.get("/api/submission/{submission_id}")
def get_submission(submission_id: str):
    """Full intelligence brief for analyst review."""
    rows = ws_query(
        f"SELECT * FROM mcp_submissions "
        f"WHERE submission_id = '{submission_id}' LIMIT 1"
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Submission not found")
    sub = rows[0]

    brief = get_intelligence_brief(sub.get("mcp_identifier", ""))

    # Check for existing decision
    decisions = ws_query(
        f"SELECT * FROM mcp_decisions "
        f"WHERE submission_id = '{submission_id}' ORDER BY decided_at DESC LIMIT 1"
    )

    return {
        "submission":   sub,
        "brief":        brief,
        "decision":     decisions[0] if decisions else None
    }


@app.post("/api/decision/{submission_id}")
def record_decision(submission_id: str, req: DecisionRequest):
    """Analyst records their decision with conditions and expiry."""
    if req.decision not in ("APPROVED", "CONDITIONAL", "REJECTED"):
        raise HTTPException(status_code=400,
            detail="decision must be APPROVED, CONDITIONAL, or REJECTED")

    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=req.expiry_days or 90)).isoformat()

    ok = ws_write("mcp_decisions", {
        "id":            next_id(),
        "submission_id": submission_id,
        "analyst_name":  req.analyst_name,
        "decision":      req.decision,
        "conditions":    req.conditions or "",
        "notes":         req.notes or "",
        "expiry_days":   req.expiry_days or 90,
        "expires_at":    expires_at,
        "decided_at":    now.isoformat()
    })
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to write decision")

    # Update submission status
    ws_write("mcp_submissions", {
        "submission_id": submission_id,
        "status":        req.decision.lower()
    })

    log.info(f"Decision {submission_id}: {req.decision} by {req.analyst_name}")
    return {
        "submission_id": submission_id,
        "decision":      req.decision,
        "expires_at":    expires_at,
        "message":       f"{req.decision} by {req.analyst_name}. Expires {expires_at[:10]}."
    }


@app.get("/api/registry")
def registry(
    page:    int = Query(1, ge=1),
    limit:   int = Query(20, ge=1, le=100),
    verdict: Optional[str] = None
):
    """Paginated list of assessed MCPs."""
    offset  = (page - 1) * limit
    where   = f"WHERE verdict = '{verdict}'" if verdict else ""
    rows    = ws_query(
        f"SELECT server_id, name, verdict, trust_score, last_assessed "
        f"FROM mcp_server_registry {where} "
        f"ORDER BY last_assessed DESC NULLS LAST "
        f"LIMIT {limit} OFFSET {offset}"
    )
    return {"page": page, "limit": limit, "results": rows}


@app.get("/api/audit")
def audit(limit: int = Query(50, ge=1, le=200)):
    """Full audit trail of all decisions."""
    rows = ws_query(
        f"SELECT d.submission_id, d.analyst_name, d.decision, d.decided_at, "
        f"d.conditions, s.mcp_identifier, s.requester_team, s.environment "
        f"FROM mcp_decisions d "
        f"JOIN mcp_submissions s ON d.submission_id = s.submission_id "
        f"ORDER BY d.decided_at DESC LIMIT {limit}"
    )
    return {"count": len(rows), "decisions": rows}


# ── Entry point ────────────────────────────────────────────────────────────────

def run():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [approval_workflow] %(levelname)s: %(message)s"
    )
    log.info(f"ZO-SENTINEL Approval Workflow starting on port {PORT}")
    uvicorn.run("approval_workflow:app",
                host="0.0.0.0", port=PORT,
                log_level="info", access_log=False)


if __name__ == "__main__":
    run()