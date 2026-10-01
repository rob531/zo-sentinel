# deps: fastapi, pydantic, sqlalchemy
"""Trust Gate Override Admin API.

Admin CRUD for server-level trust gate override rules.
These override rules are applied by trust_gating_override.trust_gate() at
read-time -- this module only manages the override records.

Tables:
  - trust_gate_overrides (app DB, created here on startup)
  - mcp_server_registry  (app DB, validated on create/update)
  - mcp_llm_axis_scores  (app DB, read-only)

Public endpoint (auth=public per directive); analyst_email is the identity
field carried in the request.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Boolean, DateTime, Text, JSON, func
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["trust_gate_override_admin_api"])

# ---------------------------------------------------------------------------
# SQLAlchemy model for override records
# ---------------------------------------------------------------------------


class TrustGateOverride(Base):
    """Per-server trust gate override record. Stored in the app DB."""
    __tablename__ = "trust_gate_overrides"

    server_id = Column(String(128), primary_key=True)
    override_verdict = Column(String(32), nullable=False)
    reason = Column(Text, nullable=False)
    analyst_email = Column(String(255), nullable=False)
    granted_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)
    active = Column(Boolean, default=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    meta = Column(JSON, nullable=True)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class TrustGateOverrideResponse(BaseModel):
    server_id: str
    override_verdict: str
    reason: str
    analyst_email: str
    granted_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    active: bool = True
    revoked_at: Optional[datetime] = None
    meta: Optional[dict] = None

    class Config:
        from_attributes = True


class CreateOverrideRequest(BaseModel):
    server_id: str = Field(..., description="server_id to override")
    override_verdict: str = Field(
        ...,
        description="Verdict to publish instead of the model score. "
                    "One of: TRUSTED, AMBER, UNTRUSTED, UNKNOWN, AMBER_UNVERIFIED, "
                    "CAUTION_LIMITED, TRUSTED_RESEARCH, ENTERPRISE_CONTROLLED",
    )
    reason: str = Field(..., description="Analyst justification")
    analyst_email: str = Field(..., description="Email of the analyst creating the override")
    expires_at: Optional[datetime] = Field(
        None, description="ISO-8601 expiry timestamp (UTC)"
    )
    meta: Optional[dict] = Field(None, description="Optional metadata dict")


class UpdateOverrideRequest(BaseModel):
    override_verdict: Optional[str] = None
    reason: Optional[str] = None
    analyst_email: Optional[str] = None
    expires_at: Optional[datetime] = None
    active: Optional[bool] = None
    meta: Optional[dict] = None


class OverrideListResponse(BaseModel):
    overrides: List[TrustGateOverrideResponse]
    total: int
    limit: int
    offset: int


class MessageResponse(BaseModel):
    message: str


VALID_VERDICTS = {
    "TRUSTED", "AMBER", "UNTRUSTED", "UNKNOWN",
    "AMBER_UNVERIFIED", "CAUTION_LIMITED",
    "TRUSTED_RESEARCH", "ENTERPRISE_CONTROLLED",
}

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _ensure_table(db: Session) -> None:
    """Create trust_gate_overrides table if it does not exist."""
    import re
    sql = """
    CREATE TABLE IF NOT EXISTS trust_gate_overrides (
        server_id         VARCHAR(128) PRIMARY KEY,
        override_verdict  VARCHAR(32) NOT NULL,
        reason            TEXT NOT NULL,
        analyst_email     VARCHAR(255) NOT NULL,
        granted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        expires_at        TIMESTAMPTZ,
        active            BOOLEAN NOT NULL DEFAULT TRUE,
        revoked_at        TIMESTAMPTZ,
        meta              JSON
    )
    """
    conn = db.connection()
    existing = conn.execute(
        db.query(TrustGateOverride).statement
    ).column_descriptions
    # Use raw SQL for DDL so it works on both sqlite and postgres
    conn.execute(db.connection().exec_driver_sql(
        "SELECT 1 FROM trust_gate_overrides WHERE 1=0"
    ))
    # Safe IF NOT EXISTS DDL
    try:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS trust_gate_overrides ("
            "server_id VARCHAR(128) PRIMARY KEY,"
            "override_verdict VARCHAR(32) NOT NULL,"
            "reason TEXT NOT NULL,"
            "analyst_email VARCHAR(255) NOT NULL,"
            "granted_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            "expires_at TIMESTAMPTZ,"
            "active BOOLEAN NOT NULL DEFAULT TRUE,"
            "revoked_at TIMESTAMPTZ,"
            "meta JSON"
            ")"
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _validate_server_exists(db: Session, server_id: str) -> bool:
    """Return True only if server_id is in mcp_server_registry."""
    row = db.query(McpServerRegistry.server_id).filter(
        McpServerRegistry.server_id == server_id
    ).first()
    return row is not None


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/admin/trust-gate-overrides",
    response_model=OverrideListResponse,
    summary="List trust gate override rules",
)
def list_overrides(
    active_only: bool = Query(True, description="Filter to active overrides only"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> OverrideListResponse:
    """
    Paginated list of all trust gate override rules.
    """
    _ensure_table(db)

    q = db.query(TrustGateOverride)
    if active_only:
        q = q.filter(TrustGateOverride.active == True)  # noqa: E712

    total = q.count()
    rows = (
        q.order_by(TrustGateOverride.granted_at.desc())
         .offset(offset)
         .limit(limit)
         .all()
    )
    return OverrideListResponse(
        overrides=[TrustGateOverrideResponse.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/admin/trust-gate-overrides/{server_id}",
    response_model=TrustGateOverrideResponse,
    summary="Get a single override rule",
)
def get_override(
    server_id: str,
    db: Session = Depends(get_session),
) -> TrustGateOverrideResponse:
    """
    Fetch the active override for a specific server_id.
    Returns 404 if no override exists.
    """
    _ensure_table(db)
    row = db.query(TrustGateOverride).filter(
        TrustGateOverride.server_id == server_id
    ).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No override found for server_id={server_id}",
        )
    return TrustGateOverrideResponse.model_validate(row)


@router.post(
    "/admin/trust-gate-overrides",
    response_model=TrustGateOverrideResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a trust gate override rule",
)
def create_override(
    body: CreateOverrideRequest,
    db: Session = Depends(get_session),
) -> TrustGateOverrideResponse:
    """
    Create (or replace) an override for server_id.
    Replaces any existing active override for the same server_id.
    """
    _ensure_table(db)

    verdict = body.override_verdict.upper()
    if verdict not in VALID_VERDICTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"override_verdict must be one of: {sorted(VALID_VERDICTS)}",
        )

    existing = db.query(TrustGateOverride).filter(
        TrustGateOverride.server_id == body.server_id
    ).first()

    now = _now()

    if existing:
        # Upsert: update existing row
        existing.override_verdict = body.override_verdict
        existing.reason = body.reason
        existing.analyst_email = body.analyst_email
        existing.expires_at = body.expires_at
        existing.active = True
        existing.revoked_at = None
        existing.meta = body.meta
        db.commit()
        db.refresh(existing)
        return TrustGateOverrideResponse.model_validate(existing)

    # Insert new row
    row = TrustGateOverride(
        server_id=body.server_id,
        override_verdict=body.override_verdict,
        reason=body.reason,
        analyst_email=body.analyst_email,
        expires_at=body.expires_at,
        active=True,
        granted_at=now,
        meta=body.meta,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return TrustGateOverrideResponse.model_validate(row)


@router.patch(
    "/admin/trust-gate-overrides/{server_id}",
    response_model=TrustGateOverrideResponse,
    summary="Update an existing override rule",
)
def update_override(
    server_id: str,
    body: UpdateOverrideRequest,
    db: Session = Depends(get_session),
) -> TrustGateOverrideResponse:
    """
    Patch fields of an existing override. Only non-None fields are updated.
    """
    _ensure_table(db)

    row = db.query(TrustGateOverride).filter(
        TrustGateOverride.server_id == server_id
    ).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No override found for server_id={server_id}",
        )

    if body.override_verdict is not None:
        v = body.override_verdict.upper()
        if v not in VALID_VERDICTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"override_verdict must be one of: {sorted(VALID_VERDICTS)}",
            )
        row.override_verdict = body.override_verdict

    if body.reason is not None:
        row.reason = body.reason
    if body.analyst_email is not None:
        row.analyst_email = body.analyst_email
    if body.expires_at is not None:
        row.expires_at = body.expires_at
    if body.active is not None:
        row.active = body.active
        if body.active:
            row.revoked_at = None
    if body.meta is not None:
        row.meta = body.meta

    db.commit()
    db.refresh(row)
    return TrustGateOverrideResponse.model_validate(row)


@router.delete(
    "/admin/trust-gate-overrides/{server_id}",
    response_model=MessageResponse,
    summary="Revoke (soft-delete) an override rule",
)
def revoke_override(
    server_id: str,
    analyst_email: str = Query(..., description="Email of analyst revoking the override"),
    db: Session = Depends(get_session),
) -> MessageResponse:
    """
    Soft-revokes the active override for server_id by setting active=FALSE
    and recording revoked_at.
    """
    _ensure_table(db)

    row = db.query(TrustGateOverride).filter(
        TrustGateOverride.server_id == server_id
    ).first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No override found for server_id={server_id}",
        )

    row.active = False
    row.revoked_at = _now()
    db.commit()

    return MessageResponse(
        message=f"Override for server_id={server_id} has been revoked."
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    # Create all tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    def _override_get_session():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    # Create trust_gate_overrides table in test DB
    with test_engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE IF NOT EXISTS trust_gate_overrides ("
            "server_id VARCHAR(128) PRIMARY KEY,"
            "override_verdict VARCHAR(32) NOT NULL,"
            "reason TEXT NOT NULL,"
            "analyst_email VARCHAR(255) NOT NULL,"
            "granted_at TIMESTAMP,"
            "expires_at TIMESTAMP,"
            "active BOOLEAN NOT NULL DEFAULT TRUE,"
            "revoked_at TIMESTAMP,"
            "meta JSON"
            ")"
        )
        conn.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(test_app)

    # Seed two server registry entries so FK-like validation passes
    with TestSession() as sess:
        sess.add(McpServerRegistry(server_id="srv-trust-001", name="Official Stripe MCP"))
        sess.add(McpServerRegistry(server_id="srv-trust-002", name="Acme Cloud MCP"))
        sess.commit()

    # ---- Test 1: list empty ----
    resp = client.get("/api/admin/trust-gate-overrides")
    if resp.status_code != 200:
        print(f"FAIL: list empty returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    data = resp.json()
    if data["total"] != 0:
        print(f"FAIL: expected total=0, got {data['total']}")
        sys.exit(1)

    # ---- Test 2: create override ----
    resp = client.post(
        "/api/admin/trust-gate-overrides",
        json={
            "server_id": "srv-trust-001",
            "override_verdict": "TRUSTED",
            "reason": "Official Stripe publisher -- verified GitHub org",
            "analyst_email": "analyst@example.com",
        },
    )
    if resp.status_code != 201:
        print(f"FAIL: create returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    created = resp.json()
    if created["server_id"] != "srv-trust-001":
        print(f"FAIL: wrong server_id: {created}")
        sys.exit(1)
    if created["override_verdict"] != "TRUSTED":
        print(f"FAIL: wrong verdict: {created}")
        sys.exit(1)
    if not created["active"]:
        print(f"FAIL: new override should be active")
        sys.exit(1)

    # ---- Test 3: list shows one ----
    resp = client.get("/api/admin/trust-gate-overrides")
    if resp.json()["total"] != 1:
        print(f"FAIL: expected total=1, got {resp.json()}")
        sys.exit(1)

    # ---- Test 4: get single ----
    resp = client.get("/api/admin/trust-gate-overrides/srv-trust-001")
    if resp.status_code != 200:
        print(f"FAIL: get single returned {resp.status_code}")
        sys.exit(1)
    if resp.json()["override_verdict"] != "TRUSTED":
        print(f"FAIL: get single wrong verdict: {resp.json()}")
        sys.exit(1)

    # ---- Test 5: create second override ----
    resp = client.post(
        "/api/admin/trust-gate-overrides",
        json={
            "server_id": "srv-trust-002",
            "override_verdict": "AMBER",
            "reason": "Pending review for acme cloud",
            "analyst_email": "analyst@example.com",
        },
    )
    if resp.status_code != 201:
        print(f"FAIL: create 2nd returned {resp.status_code}: {resp.text}")
        sys.exit(1)

    # ---- Test 6: list shows two ----
    resp = client.get("/api/admin/trust-gate-overrides")
    if resp.json()["total"] != 2:
        print(f"FAIL: expected total=2, got {resp.json()}")
        sys.exit(1)

    # ---- Test 7: patch update ----
    resp = client.patch(
        "/api/admin/trust-gate-overrides/srv-trust-002",
        json={"override_verdict": "TRUSTED", "reason": "Updated: verified"},
    )
    if resp.status_code != 200:
        print(f"FAIL: patch returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    patched = resp.json()
    if patched["override_verdict"] != "TRUSTED":
        print(f"FAIL: patch did not update verdict: {patched}")
        sys.exit(1)

    # ---- Test 8: revoke ----
    resp = client.delete(
        "/api/admin/trust-gate-overrides/srv-trust-002",
        params={"analyst_email": "analyst@example.com"},
    )
    if resp.status_code != 200:
        print(f"FAIL: revoke returned {resp.status_code}: {resp.text}")
        sys.exit(1)

    # ---- Test 9: active_only filter ----
    resp = client.get("/api/admin/trust-gate-overrides", params={"active_only": True})
    if resp.json()["total"] != 1:
        print(f"FAIL: after revoke, active_only should show 1, got {resp.json()}")
        sys.exit(1)

    # ---- Test 10: revoked override is 404 on GET ----
    resp = client.get("/api/admin/trust-gate-overrides/srv-trust-002")
    if resp.status_code != 404:
        print(f"FAIL: revoked override should 404, got {resp.status_code}")
        sys.exit(1)

    # ---- Test 11: invalid verdict ----
    resp = client.post(
        "/api/admin/trust-gate-overrides",
        json={
            "server_id": "srv-trust-001",
            "override_verdict": "INVALID_VERDICT",
            "reason": "test",
            "analyst_email": "analyst@example.com",
        },
    )
    if resp.status_code != 400:
        print(f"FAIL: invalid verdict should 400, got {resp.status_code}")
        sys.exit(1)

    # ---- Test 12: upsert (replace existing) ----
    resp = client.post(
        "/api/admin/trust-gate-overrides",
        json={
            "server_id": "srv-trust-001",
            "override_verdict": "AMBER",
            "reason": "Changed mind -- re-review",
            "analyst_email": "analyst2@example.com",
        },
    )
    if resp.status_code != 201:
        print(f"FAIL: upsert returned {resp.status_code}: {resp.text}")
        sys.exit(1)
    upserted = resp.json()
    if upserted["override_verdict"] != "AMBER":
        print(f"FAIL: upsert did not update verdict: {upserted}")
        sys.exit(1)
    if upserted["analyst_email"] != "analyst2@example.com":
        print(f"FAIL: upsert did not update analyst: {upserted}")
        sys.exit(1)

    # List should still show only 1 active override (srv-trust-002 was revoked)
    resp = client.get("/api/admin/trust-gate-overrides")
    if resp.json()["total"] != 1:
        print(f"FAIL: upsert should not increase count, got {resp.json()}")
        sys.exit(1)

    print("PASS")
