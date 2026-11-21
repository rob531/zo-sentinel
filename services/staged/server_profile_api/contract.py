"""
services/staged/server_profile_api/contract.py

Self‑contained FastAPI contract for the server profile endpoint.
Runs a self‑test when executed as a module.
"""

from __future__ import annotations

import datetime
import sys
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# ----------------------------------------------------------------------
# Database schema (SQLite in‑memory for the contract test)
# ----------------------------------------------------------------------
metadata = MetaData()

McpServerRegistry = Table(
    "mcp_server_registry",
    metadata,
    Column("server_id", String, primary_key=True),
    Column("name", String, nullable=True),
    Column("registry_source", String, nullable=True),
    Column("url", String, nullable=True),
    Column("trust_score", Float, nullable=True),
    Column("verdict", String, nullable=True),
    Column("verdict_reasoning", String, nullable=True),
    Column("confidence", Float, nullable=True),
    Column("risk_tier", String, nullable=True),
    Column("scan_count", Integer, nullable=False, default=0),
    Column("first_seen", DateTime, nullable=True),
    Column("last_seen", DateTime, nullable=True),
    Column("last_assessed", DateTime, nullable=True),
)

McpLlmAxisScore = Table(
    "mcp_llm_axis_scores",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("server_id", String, nullable=False),
    Column("axis_name", String, nullable=False),
    Column("label", String, nullable=True),
    Column("p_top", Float, nullable=True),
    Column("escalated", Boolean, nullable=False, default=False),
    Column("escalated_to", String, nullable=True),
    Column("scored_at", DateTime, nullable=False),
)

vuln_advisories = Table(
    "vuln_advisories",
    metadata,
    Column("advisory_id", String, primary_key=True),
    Column("severity", String, nullable=True),  # e.g. low, medium, high, critical
)

vuln_links = Table(
    "vuln_links",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("server_id", String, nullable=False),
    Column("advisory_id", String, nullable=False),
    Column("match_confidence", Float, nullable=True),
    Column("match_value", String, nullable=False),
)

threat_intel_refs = Table(
    "threat_intel_refs",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("indicator_value", String, nullable=False),
)

McpScoreDispute = Table(
    "mcp_score_disputes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("server_id", String, nullable=False),
    Column("status", String, nullable=False),  # e.g. open, resolved, rejected
)

# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class AxisModel(BaseModel):
    axis_name: str
    label: Optional[str] = None
    p_top: Optional[float] = None
    escalated: bool
    escalated_to: Optional[str] = None
    scored_at: Optional[datetime.datetime] = None


class VulnerabilitiesModel(BaseModel):
    total: int
    max_severity: Optional[str] = None


class ServerProfileModel(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    url: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    confidence: Optional[float] = None
    scan_count: int
    first_seen: Optional[datetime.datetime] = None
    last_seen: Optional[datetime.datetime] = None
    last_assessed: Optional[datetime.datetime] = None
    axes: List[AxisModel] = Field(default_factory=list)
    vulnerabilities: VulnerabilitiesModel
    threat_intel_hits: int
    open_disputes: int


# ----------------------------------------------------------------------
# FastAPI app & router
# ----------------------------------------------------------------------
router = APIRouter()


def get_db(engine: Engine = Depends(lambda: app.state.engine)):
    """Yield a SQLAlchemy Session bound to the provided engine."""
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get(
    "/servers/{server_id}/profile",
    response_model=ServerProfileModel,
    response_model_exclude_unset=True,
)
def get_server_profile(
    server_id: str, db: Session = Depends(get_db)
) -> ServerProfileModel:
    # ------------------------------------------------------------------
    # 1️⃣ Server registry row
    # ------------------------------------------------------------------
    server_row = db.execute(
        text(
            """
            SELECT server_id, name, registry_source, url,
                   trust_score, verdict, confidence, risk_tier,
                   scan_count, first_seen, last_seen, last_assessed
            FROM mcp_server_registry
            WHERE server_id = :sid
            """
        ),
        {"sid": server_id},
    ).first()

    if server_row is None:
        # Return a "null" profile with zero counts
        return ServerProfileModel(
            server_id=server_id,
            name=None,
            registry_source=None,
            url=None,
            trust_score=None,
            verdict=None,
            risk_tier=None,
            confidence=None,
            scan_count=0,
            first_seen=None,
            last_seen=None,
            last_assessed=None,
            axes=[],
            vulnerabilities=VulnerabilitiesModel(total=0, max_severity=None),
            threat_intel_hits=0,
            open_disputes=0,
        )

    # ------------------------------------------------------------------
    # 2️⃣ Latest axis scores per axis_name
    # ------------------------------------------------------------------
    axis_rows = db.execute(
        text(
            """
            SELECT axis_name, label, p_top, escalated, escalated_to, scored_at
            FROM mcp_llm_axis_scores AS a
            WHERE server_id = :sid
              AND scored_at = (
                  SELECT MAX(scored_at)
                  FROM mcp_llm_axis_scores
                  WHERE server_id = :sid AND axis_name = a.axis_name
              )
            ORDER BY axis_name
            """
        ),
        {"sid": server_id},
    ).fetchall()

    axes = [
        AxisModel(
            axis_name=row.axis_name,
            label=row.label,
            p_top=row.p_top,
            escalated=row.escalated,
            escalated_to=row.escalated_to,
            scored_at=row.scored_at,
        )
        for row in axis_rows
    ]

    # ------------------------------------------------------------------
    # 3️⃣ Vulnerabilities: total count and max severity
    # ------------------------------------------------------------------
    vuln_total = db.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM vuln_links
            WHERE server_id = :sid
            """
        ),
        {"sid": server_id},
    ).scalar_one()

    # Helper to rank severities (higher number = more severe)
    severity_rank = {
        "critical": 5,
        "high": 4,
        "medium": 3,
        "low": 2,
        "info": 1,
    }

    max_sev_row = db.execute(
        text(
            """
            SELECT adv.severity
            FROM vuln_links vl
            JOIN vuln_advisories adv ON adv.advisory_id = vl.advisory_id
            WHERE vl.server_id = :sid
            """
        ),
        {"sid": server_id},
    ).fetchall()

    max_severity = None
    if max_sev_row:
        # Determine the most severe string according to the ranking dict
        severities = [r.severity for r in max_sev_row if r.severity]
        if severities:
            max_severity = max(
                severities, key=lambda s: severity_rank.get(s.lower(), 0)
            )

    vulnerabilities = VulnerabilitiesModel(
        total=int(vuln_total), max_severity=max_severity
    )

    # ------------------------------------------------------------------
    # 4️⃣ Threat intel hits
    # ------------------------------------------------------------------
    threat_intel_hits = db.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM threat_intel_refs tir
            WHERE tir.indicator_value IN (
                SELECT match_value
                FROM vuln_links vl
                WHERE vl.server_id = :sid
            )
            """
        ),
        {"sid": server_id},
    ).scalar_one()

    # ------------------------------------------------------------------
    # 5️⃣ Open disputes (status not resolved/rejected)
    # ------------------------------------------------------------------
    open_disputes = db.execute(
        text(
            """
            SELECT COUNT(*) AS cnt
            FROM mcp_score_disputes
            WHERE server_id = :sid
              AND status NOT IN ('resolved', 'rejected')
            """
        ),
        {"sid": server_id},
    ).scalar_one()

    # ------------------------------------------------------------------
    # Assemble response
    # ------------------------------------------------------------------
    return ServerProfileModel(
        server_id=server_row.server_id,
        name=server_row.name,
        registry_source=server_row.registry_source,
        url=server_row.url,
        trust_score=server_row.trust_score,
        verdict=server_row.verdict,
        risk_tier=server_row.risk_tier,
        confidence=server_row.confidence,
        scan_count=server_row.scan_count,
        first_seen=server_row.first_seen,
        last_seen=server_row.last_seen,
        last_assessed=server_row.last_assessed,
        axes=axes,
        vulnerabilities=vulnerabilities,
        threat_intel_hits=int(threat_intel_hits),
        open_disputes=int(open_disputes),
    )


# ----------------------------------------------------------------------
# FastAPI app creation
# ----------------------------------------------------------------------
app = FastAPI()
app.include_router(router, prefix="/api")


# ----------------------------------------------------------------------
# Self‑test (executed when running `python -m services.staged.server_profile_api.contract`)
# ----------------------------------------------------------------------
def _seed_test_data(engine: Engine) -> None:
    """Create tables and insert a deterministic data set."""
    metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    now = datetime.datetime.utcnow()

    with SessionLocal() as session:
        # Server registry entry
        session.execute(
            McpServerRegistry.insert().values(
                server_id="server123",
                name="Test Server",
                registry_source="unit-test",
                url="https://example.com",
                trust_score=0.85,
                verdict="good",
                confidence=0.92,
                risk_tier="low",
                scan_count=7,
                first_seen=now - datetime.timedelta(days=30),
                last_seen=now - datetime.timedelta(days=1),
                last_assessed=now,
            )
        )

        # Axis scores (two distinct axes)
        session.execute(
            McpLlmAxisScore.insert(),
            [
                {
                    "server_id": "server123",
                    "axis_name": "availability",
                    "label": "high",
                    "p_top": 0.78,
                    "escalated": False,
                    "escalated_to": None,
                    "scored_at": now - datetime.timedelta(hours=2),
                },
                {
                    "server_id": "server123",
                    "axis_name": "integrity",
                    "label": "medium",
                    "p_top": 0.55,
                    "escalated": True,
                    "escalated_to": "security-team",
                    "scored_at": now - datetime.timedelta(hours=1),
                },
            ],
        )

        # Vulnerability advisory
        session.execute(
            vuln_advisories.insert().values(
                advisory_id="ADV-001",
                severity="high",
            )
        )
        # Link between server and advisory
        session.execute(
            vuln_links.insert().values(
                server_id="server123",
                advisory_id="ADV-001",
                match_confidence=0.97,
                match_value="CVE-2023-0001",
            )
        )
        # Threat intel reference that matches the vuln link
        session.execute(
            threat_intel_refs.insert().values(
                indicator_value="CVE-2023-0001",
            )
        )
        # Open dispute
        session.execute(
            McpScoreDispute.insert().values(
                server_id="server123",
                status="open",
            )
        )
        session.commit()


def _run_self_test() -> None:
    """Execute the contract acceptance test."""
    # Create an in‑memory SQLite engine with a static pool (so the same DB is shared)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    # Seed deterministic data
    _seed_test_data(engine)

    # Attach the engine to the FastAPI app state for the dependency
    app.state.engine = engine

    # Override the DB dependency for the TestClient
    def _override_get_db():
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_get_db

    client = TestClient(app)

    # 1️⃣ Known server – expect populated fields
    resp = client.get("/api/servers/server123/profile")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert data["server_id"] == "server123"
    assert data["name"] == "Test Server"
    assert data["scan_count"] == 7
    assert isinstance(data["axes"], list) and len(data["axes"]) == 2
    assert data["vulnerabilities"]["total"] == 1
    assert data["vulnerabilities"]["max_severity"] == "high"
    assert data["threat_intel_hits"] == 1
    assert data["open_disputes"] == 1

    # 2️⃣ Unknown server – all nullable, counts zero
    resp2 = client.get("/api/servers/unknown_server/profile")
    assert resp2.status_code == 200, f"Unexpected status {resp2.status_code}"
    data2 = resp2.json()
    assert data2["server_id"] == "unknown_server"
    assert data2["name"] is None
    assert data2["scan_count"] == 0
    assert data2["axes"] == []
    assert data2["vulnerabilities"]["total"] == 0
    assert data2["vulnerabilities"]["max_severity"] is None
    assert data2["threat_intel_hits"] == 0
    assert data2["open_disputes"] == 0

    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    _run_self_test()