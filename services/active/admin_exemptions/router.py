# deps: fastapi, sqlalchemy, pydantic
"""Admin Exemptions Router.

Public API for managing MCP server risk-score exemptions.
Reads/writes the mcp_exemptions table via the standard app DB session.

NOTE: McpExemption is defined inline because mcp_exemptions does not yet exist
as a model in app.models (SCHEMA_TRUTH.md lists exactly 14 classes; this table
is not among them).  This build implements the service contract assuming the
table and model exist.  A follow-on schema decision is needed to add the model
to app/models.py.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, func, update
from sqlalchemy.orm import Session, declarative_base

from app.db import get_session
from app.models import McpServerRegistry

# ---------------------------------------------------------------------------
# Inline model for mcp_exemptions (not yet in app.models)
# ---------------------------------------------------------------------------
_EXEMPTION_TABLE_NAME = "mcp_exemptions"

try:
    from app.models import Base as _AppBase
except ImportError:
    from sqlalchemy.orm import declarative_base as _declarative_base

    _AppBase = _declarative_base()


class McpExemption(_AppBase):
    __tablename__ = _EXEMPTION_TABLE_NAME

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(String, ForeignKey("mcp_server_registry.server_id"), nullable=False)
    reason = Column(String, nullable=False)
    exempted_by = Column(String, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    active = Column(Boolean, nullable=False, default=True)


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class ExemptionCreate(BaseModel):
    server_id: int = Field(..., description="ID of the server to exempt")
    reason: str = Field(..., description="Reason for exemption")
    expires_at: Optional[datetime] = Field(None, description="Expiration timestamp")


class ExemptionResponse(BaseModel):
    exemption_id: int
    server_id: int
    server_name: Optional[str] = None
    reason: str
    exempted_by: Optional[str] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    active: bool

    model_config = {"from_attributes": True}


class SummaryResponse(BaseModel):
    active: int
    inactive: int


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/api/admin/exemptions", tags=["admin_exemptions"])


def _to_response(exc: McpExemption, server_name: Optional[str] = None) -> ExemptionResponse:
    return ExemptionResponse(
        exemption_id=exc.id,
        server_id=int(exc.server_id),
        server_name=server_name,
        reason=exc.reason,
        exempted_by=exc.exempted_by,
        expires_at=exc.expires_at,
        created_at=exc.created_at,
        active=exc.active,
    )


@router.get("/", response_model=List[ExemptionResponse])
def list_exemptions(db: Session = Depends(get_session)):
    rows = (
        db.query(McpExemption, McpServerRegistry.name)
        .join(McpServerRegistry, McpExemption.server_id == McpServerRegistry.server_id)
        .filter(McpExemption.active.is_(True))
        .all()
    )
    return [_to_response(exc, name) for exc, name in rows]


@router.post("/", response_model=ExemptionResponse, status_code=status.HTTP_201_CREATED)
def create_exemption(payload: ExemptionCreate, db: Session = Depends(get_session)):
    server = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == str(payload.server_id))
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    new_exc = McpExemption(
        server_id=str(payload.server_id),
        reason=payload.reason,
        exempted_by="admin",
        expires_at=payload.expires_at,
        created_at=datetime.now(timezone.utc),
        active=True,
    )
    db.add(new_exc)
    db.flush()
    db.refresh(new_exc)
    return _to_response(new_exc, server.name)


@router.delete("/{exemption_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_exemption(exemption_id: int, db: Session = Depends(get_session)):
    stmt = (
        update(McpExemption)
        .where(McpExemption.id == exemption_id, McpExemption.active.is_(True))
        .values(active=False)
    )
    result = db.execute(stmt)
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Active exemption not found")
    db.commit()
    return


@router.get("/summary", response_model=SummaryResponse)
def exemption_summary(db: Session = Depends(get_session)):
    counts = (
        db.query(McpExemption.active, func.count())
        .group_by(McpExemption.active)
        .all()
    )
    data = {True: 0, False: 0}
    for active, cnt in counts:
        data[active] = cnt
    return SummaryResponse(active=data[True], inactive=data[False])


# ---------------------------------------------------------------------------
# Self-test (in-memory SQLite; runs only when file executed directly)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Create all tables (both from app.models Base and inline McpExemption)
    _AppBase.metadata.create_all(bind=engine)

    def get_test_session() -> Session:
        with SessionLocal() as sess:
            yield sess

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session
    client = TestClient(test_app)

    # Seed data
    with SessionLocal() as sess:
        srv1 = McpServerRegistry(server_id="1", name="server-one")
        srv2 = McpServerRegistry(server_id="2", name="server-two")
        sess.add_all([srv1, srv2])
        sess.flush()

        exc1 = McpExemption(
            server_id="1",
            reason="maintenance",
            exempted_by="admin",
            expires_at=datetime.now(timezone.utc),
            created_at=datetime.now(timezone.utc),
            active=True,
        )
        exc2 = McpExemption(
            server_id="2",
            reason="testing",
            exempted_by="admin",
            expires_at=None,
            created_at=datetime.now(timezone.utc),
            active=True,
        )
        sess.add_all([exc1, exc2])
        sess.commit()

    # POST a new exemption
    post_resp = client.post(
        "/api/admin/exemptions/",
        json={"server_id": 1, "reason": "new-reason", "expires_at": None},
    )
    assert post_resp.status_code == 201, f"POST failed: {post_resp.status_code} {post_resp.text}"

    # GET list — expect 3 active
    get_resp = client.get("/api/admin/exemptions/")
    assert get_resp.status_code == 200, f"GET failed: {get_resp.status_code}"
    assert len(get_resp.json()) == 3, f"Expected 3, got {len(get_resp.json())}"

    # DELETE the newly created exemption
    new_id = post_resp.json()["exemption_id"]
    del_resp = client.delete(f"/api/admin/exemptions/{new_id}")
    assert del_resp.status_code == 204, f"DELETE failed: {del_resp.status_code}"

    # GET summary — expect 2 active, 1 inactive
    sum_resp = client.get("/api/admin/exemptions/summary")
    assert sum_resp.status_code == 200, f"SUMMARY failed: {sum_resp.status_code}"
    summary = sum_resp.json()
    assert summary["active"] == 2, f"Expected 2 active, got {summary['active']}"
    assert summary["inactive"] == 1, f"Expected 1 inactive, got {summary['inactive']}"

    # Final GET list — still 3 total (including inactive)
    final_get = client.get("/api/admin/exemptions/")
    assert final_get.status_code == 200
    assert len(final_get.json()) == 3, f"Expected 3 total, got {len(final_get.json())}"

    print("PASS")
    sys.exit(0)
