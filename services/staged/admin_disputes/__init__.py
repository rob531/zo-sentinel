# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.
from __future__ import annotations
from typing import TYPE_CHECKING, Any, Optional
from datetime import datetime
from decimal import Decimal
import json
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, Text, DateTime, Numeric, Boolean, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Session as SQLAlchemySession

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

router = APIRouter(tags=["admin_disputes"])


class Base(DeclarativeBase):
    pass


class MCPServiceBase(Base):
    __abstract__ = True
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MCPServiceRegistry(MCPServiceBase):
    __tablename__ = "McpServerRegistry"

    name = Column(String(255), nullable=False)
    server_type = Column(String(100), nullable=False)
    description = Column(Text)
    endpoint_url = Column(Text)
    status = Column(String(50), default="active")
    config_json = Column(Text)


class McpLlmAxisScore(MCPServiceBase):
    __tablename__ = "McpLlmAxisScore"

    server_id = Column(UUID(as_uuid=True), nullable=False)
    axis_name = Column(String(100), nullable=False)
    score = Column(Numeric(10, 4))
    confidence = Column(Numeric(5, 2))
    reasoning = Column(Text)
    model_version = Column(String(100))
    evaluated_at = Column(DateTime)


class McpScoreDispute(MCPServiceBase):
    __tablename__ = "McpScoreDispute"

    server_id = Column(UUID(as_uuid=True), nullable=False)
    axis_name = Column(String(100), nullable=False)
    disputed_score = Column(Numeric(10, 4))
    claimed_score = Column(Numeric(10, 4))
    justification = Column(Text)
    dispute_status = Column(String(50), default="pending")
    resolved_by = Column(UUID(as_uuid=True))
    resolved_at = Column(DateTime)
    resolution_notes = Column(Text)


class Orgs(MCPServiceBase):
    __tablename__ = "orgs"

    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True)
    is_active = Column(Boolean, default=True)


class Users(MCPServiceBase):
    __tablename__ = "users"

    org_id = Column(UUID(as_uuid=True))
    email = Column(String(255), unique=True, nullable=False)
    name = Column(String(255))
    role = Column(String(50))


class ServiceContext(BaseModel):
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    org_id: Optional[str] = None
    user_id: Optional[str] = None
    trace_id: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True


class SignalScoreInput(BaseModel):
    server_id: str
    axis_name: str
    score: float
    confidence: float
    reasoning: Optional[str] = None
    model_version: Optional[str] = None


class SignalScoreOutput(BaseModel):
    server_id: str
    axis_name: str
    score: float
    confidence: float
    stored: bool


class MeshMemoryInput(BaseModel):
    entity_type: str
    entity_id: str
    memory: dict[str, Any]


class MeshMemoryOutput(BaseModel):
    entity_type: str
    entity_id: str
    stored: bool


class ScoreDisputeInput(BaseModel):
    server_id: str
    axis_name: str
    disputed_score: float
    claimed_score: float
    justification: str


class ScoreDisputeOutput(BaseModel):
    dispute_id: str
    status: str
    created_at: datetime


class ScoreDisputesResponse(BaseModel):
    disputes: list[dict[str, Any]]
    total: int


def get_session() -> "Session":
    from app.db import get_session as _get_session
    return _get_session()


def get_score_disputes(
    server_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
    session: "Session" = Depends(get_session),
) -> list[McpScoreDispute]:
    query = session.query(McpScoreDispute)
    if server_id:
        query = query.filter(McpScoreDispute.server_id == uuid.UUID(server_id))
    if axis_name:
        query = query.filter(McpScoreDispute.axis_name == axis_name)
    if status:
        query = query.filter(McpScoreDispute.dispute_status == status)
    return query.limit(limit).all()


@router.get("/admin/disputes", response_model=ScoreDisputesResponse)
def get_score_disputes_endpoint(
    server_id: Optional[str] = Query(None),
    axis_name: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    session: "Session" = Depends(get_session),
) -> ScoreDisputesResponse:
    disputes = get_score_disputes(server_id, axis_name, status, limit, session)
    return ScoreDisputesResponse(
        disputes=[
            {
                "id": str(d.id),
                "server_id": str(d.server_id),
                "axis_name": d.axis_name,
                "disputed_score": float(d.disputed_score) if d.disputed_score else None,
                "claimed_score": float(d.claimed_score) if d.claimed_score else None,
                "justification": d.justification,
                "status": d.dispute_status,
                "resolved_by": str(d.resolved_by) if d.resolved_by else None,
                "resolved_at": d.resolved_at.isoformat() if d.resolved_at else None,
                "resolution_notes": d.resolution_notes,
                "created_at": d.created_at.isoformat(),
                "updated_at": d.updated_at.isoformat(),
            }
            for d in disputes
        ],
        total=len(disputes),
    )


def _run_self_test():
    from app.db import get_session
    from app.models import Base as AppBase
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from pydantic import BaseModel as PydanticBase

    # Create a combined metaclass to resolve the conflict
    class CombinedMeta(type(PydanticBase), type(AppBase)):
        pass

    # Build combined model class dynamically
    ServiceConfig = CombinedMeta(
        "ServiceConfig",
        (PydanticBase, AppBase),
        {"__abstract__": False}
    )

    engine = create_engine("sqlite:///:memory:", echo=False)
    AppBase.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    try:
        # Test the service
        # FU-369: removed an import of `override_get_session` from a module that does not
        # exist in this tree. The name was never used in this file.
        from app.db import get_session as original_get_session

        app.dependency_overrides[original_get_session] = lambda: db

        # Verify router is registered
        assert router is not None
        routes = [r.path for r in router.routes]
        assert "/admin/disputes" in routes

        # Test get_score_disputes function
        disputes = get_score_disputes(session=db)
        assert isinstance(disputes, list)

        # Test endpoint function
        response = get_score_disputes_endpoint(session=db)
        assert isinstance(response, ScoreDisputesResponse)
        assert hasattr(response, "disputes")
        assert hasattr(response, "total")

        print("PASS")
    finally:
        db.close()
        app.dependency_overrides.clear()


if __name__ == "__main__":
    import app
    _run_self_test()