"""ORM models for the app tier -- the multi-tenant core (orgs/users) + API keys.
Portable column types only (String/DateTime/ForeignKey) so the same DDL runs on
sqlite (dev/CI) and Postgres (deploy).
"""
from __future__ import annotations
from datetime import datetime

from typing import Optional

from sqlalchemy import (String, Text, Integer, BigInteger, Boolean, Float, JSON,
                        DateTime, ForeignKey, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    org_id: Mapped[str] = mapped_column(String(64), ForeignKey("orgs.id"), index=True)
    role: Mapped[str] = mapped_column(String(32), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(64), ForeignKey("orgs.id"), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    label: Mapped[str] = mapped_column(String(128), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# --- Threat-intel data tables (the registry + SFT scores the app reads). These are
# the app-data tables migrating off the builder's DuckDB onto this Postgres; columns
# mirror the production DuckDB shape for a clean cutover. ---


class McpServerRegistry(Base):
    __tablename__ = "mcp_server_registry"
    server_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(512))
    registry_source: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    url: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(Text)
    trust_score: Mapped[Optional[float]] = mapped_column(Float)
    verdict: Mapped[Optional[str]] = mapped_column(String(64))
    verdict_reasoning: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    last_assessed: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    first_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_scanned: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    scan_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    risk_tier: Mapped[Optional[str]] = mapped_column(String(32))
    meta: Mapped[Optional[str]] = mapped_column("metadata", Text)


class McpLlmAxisScore(Base):
    __tablename__ = "mcp_llm_axis_scores"
    __table_args__ = (UniqueConstraint("server_id", "axis_name", "model_version",
                                       name="uq_axis_scores_natural"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    server_id: Mapped[str] = mapped_column(String(128), index=True)
    axis_name: Mapped[str] = mapped_column(String(64))
    label: Mapped[Optional[str]] = mapped_column(String(64))
    label_index: Mapped[Optional[int]] = mapped_column(Integer)
    probs: Mapped[Optional[dict]] = mapped_column(JSON)
    p_top: Mapped[Optional[float]] = mapped_column(Float)
    p_critical: Mapped[Optional[float]] = mapped_column(Float)
    p_danger: Mapped[Optional[float]] = mapped_column(Float)
    escalated: Mapped[Optional[bool]] = mapped_column(Boolean)
    escalated_to: Mapped[Optional[str]] = mapped_column(String(32))
    decision_rule_version: Mapped[Optional[str]] = mapped_column(String(32))
    model_version: Mapped[str] = mapped_column(String(64), index=True)
    adapter_sha256: Mapped[Optional[str]] = mapped_column(String(80))
    scored_at: Mapped[Optional[datetime]] = mapped_column(DateTime, server_default=func.now())


class McpScoreDispute(Base):
    """User-submitted dispute / proposed re-score for an MCP server. Admin-gated;
    short-term this is a feedback record (approve/reject only) -- a later job may
    consume approved disputes into score overrides."""
    __tablename__ = "mcp_score_disputes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    server_id: Mapped[str] = mapped_column(String(128), index=True)
    submitted_by: Mapped[str] = mapped_column(String(128), index=True)
    proposed_overall_risk: Mapped[str] = mapped_column(String(16))
    proposed_axes: Mapped[Optional[dict]] = mapped_column(JSON)
    reason_category: Mapped[str] = mapped_column(String(48))
    explanation: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    admin_note: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
