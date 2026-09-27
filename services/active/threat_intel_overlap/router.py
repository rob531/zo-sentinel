# deps: fastapi, pydantic, sqlalchemy
"""Threat Intel Overlap Service.

Detects overlap between threat intelligence indicators and registered MCP servers.
Identifies servers appearing in threat intel feeds, cross-references indicators
across sources, and surfaces high-confidence threat-server matches.
Reads from app Postgres (McpServerRegistry, ThreatIntelRef) via get_session.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, McpServerRegistry, ThreatIntelRef

router = APIRouter(prefix="/api", tags=["threat_intel_overlap"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class ServerMatchRecord(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    trust_score: Optional[float] = None
    verdict: Optional[str] = None
    matched_indicators: List[str]
    pulse_count: int
    source_count: int
    latest_fetch: Optional[str] = None


class OverlapSummary(BaseModel):
    total_servers: int
    servers_with_threat_intel: int
    overlap_pct: float
    by_risk_tier: Dict[str, int]
    by_source: Dict[str, int]
    top_pulses: List[dict]
    top_indicators: List[dict]


class PulseOverlap(BaseModel):
    pulse_id: str
    pulse_name: Optional[str] = None
    is_aggregator: bool
    indicator_count: int
    matched_server_count: int
    matched_servers: List[dict]


class SourceCorrelation(BaseModel):
    source: str
    total_refs: int
    unique_indicators: int
    matched_servers: int
    pulse_count: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _iso(val: Optional[datetime]) -> Optional[str]:
    return val.isoformat() if val else None


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/threat-intel-overlap/summary",
    response_model=OverlapSummary,
    name="threat_intel_overlap:summary",
)
def get_overlap_summary(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> OverlapSummary:
    """Return overlap statistics between threat intel indicators and the server registry."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total_servers = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    # Servers that have at least one threat intel reference
    server_ids_with_ti = (
        db.query(ThreatIntelRef.indicator_value)
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
        )
        .distinct()
        .subquery()
    )
    servers_with_ti = (
        db.query(func.count(McpServerRegistry.server_id))
        .filter(McpServerRegistry.server_id.in_(db.query(server_ids_with_ti)))
        .scalar()
        or 0
    )

    overlap_pct = round((servers_with_ti / total_servers) * 100, 2) if total_servers else 0.0

    # By risk tier for matched servers
    tier_rows = (
        db.query(
            McpServerRegistry.risk_tier,
            func.count(McpServerRegistry.server_id).label("cnt"),
        )
        .filter(
            McpServerRegistry.server_id.in_(db.query(server_ids_with_ti)),
            McpServerRegistry.risk_tier.isnot(None),
        )
        .group_by(McpServerRegistry.risk_tier)
        .all()
    )
    by_risk_tier = {r.risk_tier: r.cnt for r in tier_rows}

    # By source
    source_rows = (
        db.query(
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id).label("cnt"),
        )
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
        )
        .group_by(ThreatIntelRef.source)
        .all()
    )
    by_source = {r.source or "UNKNOWN": r.cnt for r in source_rows}

    # Top pulses by indicator count
    top_pulses = (
        db.query(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            func.count(ThreatIntelRef.id).label("cnt"),
        )
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
            ThreatIntelRef.pulse_id.isnot(None),
        )
        .group_by(ThreatIntelRef.pulse_id, ThreatIntelRef.pulse_name)
        .order_by(func.count(ThreatIntelRef.id).desc())
        .limit(5)
        .all()
    )
    top_pulses_list = [
        {"pulse_id": r.pulse_id, "pulse_name": r.pulse_name, "indicator_count": r.cnt}
        for r in top_pulses
    ]

    # Top indicators (most-referenced server IDs)
    top_indicators = (
        db.query(
            ThreatIntelRef.indicator_value,
            func.count(ThreatIntelRef.id).label("ref_count"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulse_count"),
        )
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
        )
        .group_by(ThreatIntelRef.indicator_value)
        .order_by(func.count(ThreatIntelRef.id).desc())
        .limit(5)
        .all()
    )
    top_indicators_list = [
        {
            "indicator_value": r.indicator_value,
            "ref_count": r.ref_count,
            "pulse_count": r.pulse_count,
        }
        for r in top_indicators
    ]

    return OverlapSummary(
        total_servers=total_servers,
        servers_with_threat_intel=servers_with_ti,
        overlap_pct=overlap_pct,
        by_risk_tier=by_risk_tier,
        by_source=by_source,
        top_pulses=top_pulses_list,
        top_indicators=top_indicators_list,
    )


@router.get(
    "/threat-intel-overlap/servers",
    response_model=List[ServerMatchRecord],
    name="threat_intel_overlap:servers",
)
def list_matched_servers(
    days: int = Query(default=30, ge=1, le=365),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    min_pulses: int = Query(1, ge=1),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> List[ServerMatchRecord]:
    """Return servers that have threat intel indicators, with match metadata."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Subquery: server IDs with pulse count >= min_pulses
    pulse_counts = (
        db.query(
            ThreatIntelRef.indicator_value,
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulse_cnt"),
        )
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
        )
        .group_by(ThreatIntelRef.indicator_value)
        .having(func.count(func.distinct(ThreatIntelRef.pulse_id)) >= min_pulses)
        .subquery()
    )

    q = (
        db.query(McpServerRegistry, func.max(ThreatIntelRef.fetched_at).label("latest_fetch"))
        .join(ThreatIntelRef, ThreatIntelRef.indicator_value == McpServerRegistry.server_id)
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
            McpServerRegistry.server_id.in_(
                db.query(pulse_counts.c.indicator_value)
            ),
        )
    )

    if risk_tier:
        q = q.filter(McpServerRegistry.risk_tier == risk_tier)

    rows = (
        q.group_by(McpServerRegistry.server_id)
        .order_by(func.count(func.distinct(ThreatIntelRef.pulse_id)).desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    results = []
    for srv, latest_fetch in rows:
        # Gather all matched indicators for this server
        ind_rows = (
            db.query(ThreatIntelRef)
            .filter(
                ThreatIntelRef.indicator_type == "server",
                ThreatIntelRef.indicator_value == srv.server_id,
                ThreatIntelRef.fetched_at >= cutoff,
            )
            .all()
        )
        matched_indicators = list({r.pulse_id for r in ind_rows if r.pulse_id})
        source_set = {r.source for r in ind_rows if r.source}

        results.append(
            ServerMatchRecord(
                server_id=srv.server_id,
                name=srv.name,
                registry_source=srv.registry_source,
                risk_tier=srv.risk_tier,
                trust_score=srv.trust_score,
                verdict=srv.verdict,
                matched_indicators=matched_indicators,
                pulse_count=len(matched_indicators),
                source_count=len(source_set),
                latest_fetch=_iso(latest_fetch),
            )
        )
    return results


@router.get(
    "/threat-intel-overlap/pulses",
    response_model=List[PulseOverlap],
    name="threat_intel_overlap:pulses",
)
def list_pulse_overlaps(
    days: int = Query(default=30, ge=1, le=365),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> List[PulseOverlap]:
    """Return threat pulses with overlap counts against the server registry."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # All server-type indicators within window
    server_indicators = (
        db.query(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.indicator_value,
        )
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
            ThreatIntelRef.pulse_id.isnot(None),
        )
        .subquery()
    )

    # Correlated server registry rows
    matched = (
        db.query(server_indicators.c.pulse_id, McpServerRegistry.server_id)
        .join(McpServerRegistry, McpServerRegistry.server_id == server_indicators.c.indicator_value)
        .distinct()
        .subquery()
    )

    pulse_stats = (
        db.query(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
            func.count(ThreatIntelRef.id).label("ind_cnt"),
        )
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
            ThreatIntelRef.pulse_id.isnot(None),
        )
        .group_by(
            ThreatIntelRef.pulse_id,
            ThreatIntelRef.pulse_name,
            ThreatIntelRef.is_aggregator,
        )
        .subquery()
    )

    rows = (
        db.query(
            pulse_stats,
            func.count(func.distinct(matched.c.server_id)).label("srv_cnt"),
        )
        .outerjoin(matched, matched.c.pulse_id == pulse_stats.c.pulse_id)
        .group_by(
            pulse_stats.c.pulse_id,
            pulse_stats.c.pulse_name,
            pulse_stats.c.is_aggregator,
            pulse_stats.c.ind_cnt,
        )
        .order_by(pulse_stats.c.ind_cnt.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return [
        PulseOverlap(
            pulse_id=row.pulse_id,
            pulse_name=row.pulse_name,
            is_aggregator=row.is_aggregator or False,
            indicator_count=row.ind_cnt,
            matched_server_count=row.srv_cnt or 0,
            matched_servers=[],
        )
        for row in rows
    ]


@router.get(
    "/threat-intel-overlap/pulses/{pulse_id}",
    response_model=PulseOverlap,
    name="threat_intel_overlap:pulse_detail",
)
def get_pulse_overlap_detail(
    pulse_id: str,
    db: Session = Depends(get_session),
) -> PulseOverlap:
    """Return detail on a specific pulse's overlap with the server registry."""
    first = (
        db.query(ThreatIntelRef)
        .filter(
            ThreatIntelRef.pulse_id == pulse_id,
            ThreatIntelRef.indicator_type == "server",
        )
        .first()
    )
    if not first:
        raise HTTPException(status_code=404, detail="Pulse not found")

    ind_rows = (
        db.query(ThreatIntelRef)
        .filter(
            ThreatIntelRef.pulse_id == pulse_id,
            ThreatIntelRef.indicator_type == "server",
        )
        .all()
    )

    matched_servers = []
    for ind in ind_rows:
        srv = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id == ind.indicator_value)
            .first()
        )
        if srv:
            matched_servers.append(
                {
                    "server_id": srv.server_id,
                    "name": srv.name,
                    "registry_source": srv.registry_source,
                    "risk_tier": srv.risk_tier,
                    "trust_score": srv.trust_score,
                    "verdict": srv.verdict,
                    "fetched_at": _iso(ind.fetched_at),
                }
            )

    return PulseOverlap(
        pulse_id=pulse_id,
        pulse_name=first.pulse_name,
        is_aggregator=first.is_aggregator or False,
        indicator_count=len(ind_rows),
        matched_server_count=len(matched_servers),
        matched_servers=matched_servers,
    )


@router.get(
    "/threat-intel-overlap/sources",
    response_model=List[SourceCorrelation],
    name="threat_intel_overlap:sources",
)
def list_source_correlations(
    days: int = Query(default=30, ge=1, le=365),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> List[SourceCorrelation]:
    """Return per-source threat intel overlap statistics against the server registry."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id).label("total_refs"),
            func.count(func.distinct(ThreatIntelRef.indicator_value)).label("unique_indicators"),
            func.count(func.distinct(
                db.query(McpServerRegistry.server_id)
                .join(ThreatIntelRef, ThreatIntelRef.indicator_value == McpServerRegistry.server_id)
                .filter(
                    ThreatIntelRef.source == ThreatIntelRef.source,
                    ThreatIntelRef.indicator_type == "server",
                    ThreatIntelRef.fetched_at >= cutoff,
                )
                .subquery()
            )).label("matched_servers"),
        )
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.fetched_at >= cutoff,
            ThreatIntelRef.source.isnot(None),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(func.count(ThreatIntelRef.id).desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    # Compute matched_servers per source more directly
    results = []
    for row in rows:
        matched = (
            db.query(func.count(func.distinct(McpServerRegistry.server_id)))
            .join(ThreatIntelRef, ThreatIntelRef.indicator_value == McpServerRegistry.server_id)
            .filter(
                ThreatIntelRef.source == row.source,
                ThreatIntelRef.indicator_type == "server",
                ThreatIntelRef.fetched_at >= cutoff,
            )
            .scalar()
            or 0
        )
        pulse_cnt = (
            db.query(func.count(func.distinct(ThreatIntelRef.pulse_id)))
            .filter(
                ThreatIntelRef.source == row.source,
                ThreatIntelRef.indicator_type == "server",
                ThreatIntelRef.fetched_at >= cutoff,
            )
            .scalar()
            or 0
        )
        results.append(
            SourceCorrelation(
                source=row.source or "UNKNOWN",
                total_refs=row.total_refs,
                unique_indicators=row.unique_indicators,
                matched_servers=matched,
                pulse_count=pulse_cnt,
            )
        )
    return results


@router.get(
    "/threat-intel-overlap/cross-reference",
    response_model=List[dict],
    name="threat_intel_overlap:cross_reference",
)
def cross_reference_indicators(
    server_id: str = Query(..., description="Server ID to cross-reference"),
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_session),
) -> List[dict]:
    """Return all threat intel indicators (of any type) linked to a given server,
    including indicators that share a pulse with the server's own indicator."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    srv = (
        db.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not srv:
        raise HTTPException(status_code=404, detail="Server not found")

    # Find all pulses this server appears in
    pulse_ids = (
        db.query(ThreatIntelRef.pulse_id)
        .filter(
            ThreatIntelRef.indicator_type == "server",
            ThreatIntelRef.indicator_value == server_id,
            ThreatIntelRef.fetched_at >= cutoff,
            ThreatIntelRef.pulse_id.isnot(None),
        )
        .distinct()
        .all()
    )
    pulse_ids = [p[0] for p in pulse_ids]

    # Find all other indicators in those pulses
    cross_rows = (
        db.query(ThreatIntelRef)
        .filter(
            ThreatIntelRef.pulse_id.in_(pulse_ids),
            ThreatIntelRef.fetched_at >= cutoff,
        )
        .order_by(ThreatIntelRef.fetched_at.desc())
        .all()
    )

    seen = set()
    results = []
    for r in cross_rows:
        key = (r.indicator_type, r.indicator_value)
        if key not in seen:
            seen.add(key)
            results.append(
                {
                    "indicator_type": r.indicator_type,
                    "indicator_value": r.indicator_value,
                    "pulse_id": r.pulse_id,
                    "pulse_name": r.pulse_name,
                    "source": r.source,
                    "is_aggregator": r.is_aggregator or False,
                    "fetched_at": _iso(r.fetched_at),
                }
            )
    return results


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    _eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=_eng)
    _TS = sessionmaker(bind=_eng, autoflush=False, autocommit=False)

    with _TS() as db:
        # Register servers
        db.execute(
            text(
                """
            INSERT INTO mcp_server_registry
            (server_id, name, registry_source, risk_tier, trust_score, verdict, url)
            VALUES
                ('srv1','Malicious Server','github','HIGH',25.0,'THREAT','https://github.com/srv1'),
                ('srv2','Suspicious Server','npm','MEDIUM',50.0,'ELEVATED','https://npmjs.com/srv2'),
                ('srv3','Clean Server','github','LOW',95.0,'TRUSTED','https://github.com/srv3'),
                ('srv4','Unknown Server','github',NULL,NULL,NULL,'https://github.com/srv4');
            """
            )
        )
        # Register threat intel
        db.execute(
            text(
                """
            INSERT INTO threat_intel_refs
            (indicator_type, indicator_value, pulse_id, pulse_name, pulse_created,
             is_aggregator, source, source_url, fetched_at)
            VALUES
                ('server','srv1','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-01'),
                ('server','srv2','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-02'),
                ('server','srv1','pulse2','Suspicious IPs','2023-02-01',0,'alienvault','https://av/2','2023-06-03'),
                ('ip','1.2.3.4','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-01'),
                ('domain','evil.com','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-01'),
                ('server','srv3','pulse3','Grey List','2023-03-01',0,'vt','https://vt/3','2023-06-04'),
                ('server','srv1','pulse4','Aggregator Alert','2023-04-01',1,'hybrid','https://hy/4','2023-06-05'),
                ('server','srv4','pulse4','Aggregator Alert','2023-04-01',1,'hybrid','https://hy/4','2023-06-06');
            """
            )
        )
        db.commit()

    _that_app = FastAPI()
    _that_app.include_router(router)

    def _override_session():
        s = _TS()
        try:
            yield s
        finally:
            s.close()

    _that_app.dependency_overrides[get_session] = _override_session
    _c = TestClient(_that_app)

    # Test 1: overlap summary
    resp = _c.get("/api/threat-intel-overlap/summary")
    assert resp.status_code == 200, f"Summary failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["total_servers"] == 4, f"Expected 4 total servers, got {data['total_servers']}"
    assert data["servers_with_threat_intel"] == 4, (
        f"Expected 4 servers with TI, got {data['servers_with_threat_intel']}"
    )
    assert "HIGH" in data["by_risk_tier"], f"HIGH tier missing: {data['by_risk_tier']}"
    assert "otx" in data["by_source"], f"otx source missing: {data['by_source']}"
    assert len(data["top_pulses"]) >= 1
    assert data["top_pulses"][0]["pulse_id"] == "pulse1"

    # Test 2: matched servers
    resp = _c.get("/api/threat-intel-overlap/servers")
    assert resp.status_code == 200, f"Servers failed: {resp.status_code} {resp.text}"
    servers = resp.json()
    assert len(servers) == 4, f"Expected 4 matched servers, got {len(servers)}"
    srv1 = next(s for s in servers if s["server_id"] == "srv1")
    assert srv1["pulse_count"] >= 2, f"srv1 should appear in 2+ pulses, got {srv1['pulse_count']}"
    assert srv1["risk_tier"] == "HIGH"

    # Test 3: filter by risk tier
    resp = _c.get("/api/threat-intel-overlap/servers?risk_tier=HIGH")
    assert resp.status_code == 200
    filtered = resp.json()
    assert len(filtered) == 1, f"Expected 1 HIGH server, got {len(filtered)}"
    assert filtered[0]["server_id"] == "srv1"

    # Test 4: min pulses filter
    resp = _c.get("/api/threat-intel-overlap/servers?min_pulses=2")
    assert resp.status_code == 200
    multi = resp.json()
    # srv1 is in pulse1, pulse2, pulse4 = 3 pulses; srv2 in pulse1 only
    assert all(s["pulse_count"] >= 2 for s in multi), f"Some servers have <2 pulses: {multi}"
    assert any(s["server_id"] == "srv1" for s in multi)

    # Test 5: pulse overlaps list
    resp = _c.get("/api/threat-intel-overlap/pulses")
    assert resp.status_code == 200, f"Pulse list failed: {resp.status_code} {resp.text}"
    pulses = resp.json()
    assert len(pulses) >= 3, f"Expected at least 3 pulses, got {len(pulses)}"
    pulse1 = next((p for p in pulses if p["pulse_id"] == "pulse1"), None)
    assert pulse1 is not None, "pulse1 not found"
    assert pulse1["is_aggregator"] is True
    assert pulse1["indicator_count"] == 2, f"pulse1 should have 2 server indicators, got {pulse1['indicator_count']}"

    # Test 6: pulse detail
    resp = _c.get("/api/threat-intel-overlap/pulses/pulse1")
    assert resp.status_code == 200, f"Pulse detail failed: {resp.status_code} {resp.text}"
    detail = resp.json()
    assert detail["pulse_id"] == "pulse1"
    assert detail["is_aggregator"] is True
    assert detail["indicator_count"] == 2
    assert detail["matched_server_count"] == 2, f"Expected 2 matched servers, got {detail['matched_server_count']}"
    matched_ids = {s["server_id"] for s in detail["matched_servers"]}
    assert "srv1" in matched_ids and "srv2" in matched_ids

    # Test 7: pulse not found
    resp = _c.get("/api/threat-intel-overlap/pulses/nonexistent")
    assert resp.status_code == 404

    # Test 8: source correlations
    resp = _c.get("/api/threat-intel-overlap/sources")
    assert resp.status_code == 200, f"Sources failed: {resp.status_code} {resp.text}"
    sources = resp.json()
    assert len(sources) >= 3, f"Expected at least 3 sources, got {len(sources)}"
    otx = next((s for s in sources if s["source"] == "otx"), None)
    assert otx is not None
    assert otx["matched_servers"] == 2, f"otx should match 2 servers, got {otx['matched_servers']}"

    # Test 9: cross-reference
    resp = _c.get("/api/threat-intel-overlap/cross-reference?server_id=srv1")
    assert resp.status_code == 200, f"Cross-ref failed: {resp.status_code} {resp.text}"
    xref = resp.json()
    # srv1 appears in pulse1, pulse2, pulse4
    # pulse1 has: server srv1, server srv2, ip 1.2.3.4, domain evil.com
    # pulse2 has: server srv1
    # pulse4 has: server srv1, server srv4
    # Deduplicated: srv1, srv2, 1.2.3.4, evil.com, srv4 = 5 unique indicators
    assert len(xref) >= 4, f"Expected >=4 cross-ref results, got {len(xref)}"
    types = {r["indicator_type"] for r in xref}
    assert "server" in types
    types.discard("server")
    assert len(types) >= 1, "Should have non-server indicators in cross-ref"

    # Test 10: cross-reference 404 for unknown server
    resp = _c.get("/api/threat-intel-overlap/cross-reference?server_id=unknown")
    assert resp.status_code == 404

    # Test 11: pagination on servers
    resp = _c.get("/api/threat-intel-overlap/servers?skip=0&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["server_id"] == "srv1"  # srv1 has most pulses

    # Test 12: pagination on pulses
    resp = _c.get("/api/threat-intel-overlap/pulses?skip=0&limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    print("PASS")
