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
    # Clerk identity (migration 0011). Nullable: the password and OAuth-stub
    # paths have no Clerk id and must keep working untouched.
    # clerk_synced_via is a NEGATIVE CONTROL, not a label -- see 0011's
    # docstring. A row the nightly reconcile had to create for a signup that
    # is already hours old is the evidence that the webhook did not deliver.
    clerk_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    clerk_synced_via: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    clerk_created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


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


class Perspective(Base):
    """Admin-built saved facet filter over the scored registry -- the v1.1
    "Perspectives" unit (deterministic, governable discovery + the trust-diff
    attach point). facet_filters example:
    {"risk_tier": ["HIGH"], "axis:auth_strength": ["WEAK"]}."""
    __tablename__ = "perspectives"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    org_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    facet_filters: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class PerspectiveSnapshot(Base):
    """Point-in-time membership {server_id: risk_tier} of a perspective --
    the reference set trust-diff compares against."""
    __tablename__ = "perspective_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    perspective_id: Mapped[str] = mapped_column(String(64), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    membership: Mapped[Optional[dict]] = mapped_column(JSON)


class PerspectiveEvent(Base):
    """In-app trust-diff notification: one row per membership/tier change
    (entered | left | tier_changed). External connectors stay parked; webhooks
    later consume THESE rows."""
    __tablename__ = "perspective_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    perspective_id: Mapped[str] = mapped_column(String(64), index=True)
    server_id: Mapped[str] = mapped_column(String(128), index=True)
    change_type: Mapped[str] = mapped_column(String(16))
    old_tier: Mapped[Optional[str]] = mapped_column(String(32))
    new_tier: Mapped[Optional[str]] = mapped_column(String(32))
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AskCorpusDoc(Base):
    """Ask-MCPLookup lexical corpus: one snippet + field-scoped term index per
    scored server. Rebuilt idempotently by ask_corpus_indexer (content_hash
    short-circuits unchanged rows)."""
    __tablename__ = "ask_corpus_index"
    server_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    snippet: Mapped[Optional[str]] = mapped_column(Text)
    terms: Mapped[Optional[dict]] = mapped_column(JSON)
    content_hash: Mapped[Optional[str]] = mapped_column(String(32))
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class VulnAdvisory(Base):
    """A vulnerability advisory ingested from a public feed (OSV / GHSA / NVD).
    Provenance is FIRST-CLASS (THE LINE 2026-07-02: no vuln claim without a
    verifiable source): source_url + fetched_at + feed are non-null contracts."""
    __tablename__ = "vuln_advisories"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)   # e.g. CVE-2025-49596 / GHSA-...
    feed: Mapped[str] = mapped_column(String(16))                   # osv | ghsa | nvd
    summary: Mapped[Optional[str]] = mapped_column(Text)
    severity: Mapped[Optional[str]] = mapped_column(String(16))     # CRITICAL/HIGH/MEDIUM/LOW/UNKNOWN
    ecosystem: Mapped[Optional[str]] = mapped_column(String(32), index=True)  # npm/PyPI/GitHub
    package: Mapped[Optional[str]] = mapped_column(String(256), index=True)
    affected_ranges: Mapped[Optional[dict]] = mapped_column(JSON)   # normalized version ranges
    aliases: Mapped[Optional[dict]] = mapped_column(JSON)           # cross-feed ids
    source_url: Mapped[str] = mapped_column(Text)                   # THE provenance anchor
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    identities: Mapped[Optional[dict]] = mapped_column(JSON)  # precomputed canonical keys for deterministic linkage
    content_hash: Mapped[Optional[str]] = mapped_column(String(32))


class VulnLink(Base):
    """A DETERMINISTIC linkage between an advisory and a registry server
    (exact package/repo identity match -- never fuzzy). Carries match_confidence
    + match_basis so every downstream claim can prove HOW it was linked."""
    __tablename__ = "vuln_links"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    advisory_id: Mapped[str] = mapped_column(String(64), index=True)
    server_id: Mapped[str] = mapped_column(String(128), index=True)
    match_basis: Mapped[str] = mapped_column(String(32))           # repo_exact | package_exact
    match_value: Mapped[str] = mapped_column(String(256))          # the identity that matched
    match_confidence: Mapped[float] = mapped_column(Float)         # 1.0 exact only in v1
    linked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("advisory_id", "server_id", name="uq_advisory_server"),)


class ThreatIntelRef(Base):
    """An OTX pulse reference to one of OUR indicators (a linked CVE or a
    hosting domain). CONTEXT layer over exact vuln links -- never a linkage
    source (THE LINE). Provenance first-class: pulse id/name/created +
    source_url + fetched_at. is_aggregator separates bulk CVE-roundup pulses
    from curated reports."""
    __tablename__ = "threat_intel_refs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    indicator_type: Mapped[str] = mapped_column(String(16), index=True)    # cve | domain
    indicator_value: Mapped[str] = mapped_column(String(256), index=True)
    pulse_id: Mapped[str] = mapped_column(String(64))
    pulse_name: Mapped[Optional[str]] = mapped_column(String(512))
    pulse_created: Mapped[Optional[datetime]] = mapped_column(DateTime)
    is_aggregator: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16))                        # otx
    source_url: Mapped[str] = mapped_column(Text)                          # THE provenance anchor
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    __table_args__ = (UniqueConstraint("indicator_type", "indicator_value",
                                       "pulse_id", name="uq_indicator_pulse"),)


class CadenceJobRun(Base):
    """One row per run of a cadence job (CofC write-path ruling 2026-07-08:
    docs/DECISION_CADENCE_WRITE_PATH_2026_07_08.md). status: running|ok|failed."""
    __tablename__ = "cadence_job_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = mapped_column(DateTime)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    rows_affected: Mapped[Optional[int]] = mapped_column(Integer)
    detail: Mapped[Optional[dict]] = mapped_column(JSON)
