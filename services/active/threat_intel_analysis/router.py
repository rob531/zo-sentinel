# deps: fastapi, pydantic, sqlalchemy
"""Threat Intel Analysis Service.

Provides threat intelligence analysis endpoints: aggregate stats, indicator lookup,
pulse correlation, and server threat exposure.
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

router = APIRouter(prefix="/api", tags=["threat_intel_analysis"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class ThreatIndicator(BaseModel):
    id: int
    indicator_type: str
    indicator_value: str
    pulse_id: Optional[str] = None
    pulse_name: Optional[str] = None
    source: Optional[str] = None
    source_url: Optional[str] = None
    fetched_at: Optional[str] = None


class ThreatIntelSummary(BaseModel):
    total_indicators: int
    by_type: Dict[str, int]
    by_source: Dict[str, int]
    pulses: int
    aggregators: int


class ServerThreatExposure(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None
    risk_tier: Optional[str] = None
    indicator_count: int
    pulse_count: int
    latest_fetch: Optional[str] = None


class PulseDetail(BaseModel):
    pulse_id: str
    pulse_name: Optional[str] = None
    pulse_created: Optional[str] = None
    is_aggregator: bool
    indicator_count: int
    indicators: List[ThreatIndicator]


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/threat-intel/summary",
    response_model=ThreatIntelSummary,
    name="threat_intel:summary",
)
def get_threat_intel_summary(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ThreatIntelSummary:
    """Return aggregate threat intel statistics."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total = db.query(func.count(ThreatIntelRef.id)).filter(
        ThreatIntelRef.fetched_at >= cutoff
    ).scalar() or 0

    by_type_rows = (
        db.query(
            ThreatIntelRef.indicator_type,
            func.count(ThreatIntelRef.id).label("cnt"),
        )
        .filter(ThreatIntelRef.fetched_at >= cutoff)
        .group_by(ThreatIntelRef.indicator_type)
        .all()
    )
    by_type = {row.indicator_type: row.cnt for row in by_type_rows}

    by_source_rows = (
        db.query(
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id).label("cnt"),
        )
        .filter(ThreatIntelRef.fetched_at >= cutoff)
        .group_by(ThreatIntelRef.source)
        .all()
    )
    by_source = {row.source or "UNKNOWN": row.cnt for row in by_source_rows}

    pulses = (
        db.query(func.count(func.distinct(ThreatIntelRef.pulse_id)))
        .filter(
            ThreatIntelRef.fetched_at >= cutoff,
            ThreatIntelRef.pulse_id.isnot(None),
        )
        .scalar()
        or 0
    )

    aggregators = (
        db.query(func.count(ThreatIntelRef.id))
        .filter(
            ThreatIntelRef.fetched_at >= cutoff,
            ThreatIntelRef.is_aggregator == True,  # noqa: E712
        )
        .scalar()
        or 0
    )

    return ThreatIntelSummary(
        total_indicators=total,
        by_type=by_type,
        by_source=by_source,
        pulses=pulses,
        aggregators=aggregators,
    )


@router.get(
    "/threat-intel/indicators",
    response_model=List[ThreatIndicator],
    name="threat_intel:indicators",
)
def list_indicators(
    indicator_type: Optional[str] = Query(None, description="Filter by indicator type (e.g., ip, domain, url)"),
    source: Optional[str] = Query(None, description="Filter by source"),
    pulse_id: Optional[str] = Query(None, description="Filter by pulse ID"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> List[ThreatIndicator]:
    """List threat indicators with optional filters."""
    q = db.query(ThreatIntelRef)
    if indicator_type:
        q = q.filter(ThreatIntelRef.indicator_type == indicator_type)
    if source:
        q = q.filter(ThreatIntelRef.source == source)
    if pulse_id:
        q = q.filter(ThreatIntelRef.pulse_id == pulse_id)

    rows = q.order_by(ThreatIntelRef.fetched_at.desc()).offset(skip).limit(limit).all()

    return [
        ThreatIndicator(
            id=r.id,
            indicator_type=r.indicator_type,
            indicator_value=r.indicator_value,
            pulse_id=r.pulse_id,
            pulse_name=r.pulse_name,
            source=r.source,
            source_url=r.source_url,
            fetched_at=r.fetched_at.isoformat() if r.fetched_at else None,
        )
        for r in rows
    ]


@router.get(
    "/threat-intel/pulses/{pulse_id}",
    response_model=PulseDetail,
    name="threat_intel:pulse_detail",
)
def get_pulse_detail(
    pulse_id: str,
    db: Session = Depends(get_session),
) -> PulseDetail:
    """Return detailed information about a specific threat pulse."""
    first = (
        db.query(ThreatIntelRef)
        .filter(ThreatIntelRef.pulse_id == pulse_id)
        .first()
    )
    if not first:
        raise HTTPException(status_code=404, detail="Pulse not found")

    indicators = (
        db.query(ThreatIntelRef)
        .filter(ThreatIntelRef.pulse_id == pulse_id)
        .order_by(ThreatIntelRef.fetched_at.desc())
        .all()
    )

    return PulseDetail(
        pulse_id=pulse_id,
        pulse_name=first.pulse_name,
        pulse_created=first.pulse_created.isoformat() if first.pulse_created else None,
        is_aggregator=first.is_aggregator,
        indicator_count=len(indicators),
        indicators=[
            ThreatIndicator(
                id=r.id,
                indicator_type=r.indicator_type,
                indicator_value=r.indicator_value,
                pulse_id=r.pulse_id,
                pulse_name=r.pulse_name,
                source=r.source,
                source_url=r.source_url,
                fetched_at=r.fetched_at.isoformat() if r.fetched_at else None,
            )
            for r in indicators
        ],
    )


@router.get(
    "/threat-intel/servers/exposure",
    response_model=List[ServerThreatExposure],
    name="threat_intel:servers_exposure",
)
def list_servers_with_threat_exposure(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    min_indicators: int = Query(1, ge=1),
    db: Session = Depends(get_session),
) -> List[ServerThreatExposure]:
    """Return servers that have associated threat indicators, with exposure counts."""
    # Subquery: servers that have at least min_indicators linked via indicator_value match
    subq = (
        db.query(
            ThreatIntelRef.indicator_value,
            func.count(ThreatIntelRef.id).label("ind_cnt"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulse_cnt"),
            func.max(ThreatIntelRef.fetched_at).label("latest_fetch"),
        )
        .filter(ThreatIntelRef.indicator_type == "server")
        .group_by(ThreatIntelRef.indicator_value)
        .having(func.count(ThreatIntelRef.id) >= min_indicators)
        .subquery()
    )

    servers = db.query(McpServerRegistry).join(
        subq, McpServerRegistry.server_id == subq.c.indicator_value
    ).offset(skip).limit(limit).all()

    results = []
    for srv in servers:
        ind_row = db.query(subq).filter(subq.c.indicator_value == srv.server_id).first()
        results.append(
            ServerThreatExposure(
                server_id=srv.server_id,
                name=srv.name,
                registry_source=srv.registry_source,
                risk_tier=srv.risk_tier,
                indicator_count=ind_row.ind_cnt if ind_row else 0,
                pulse_count=ind_row.pulse_cnt if ind_row else 0,
                latest_fetch=(
                    ind_row.latest_fetch.isoformat()
                    if ind_row and ind_row.latest_fetch else None
                ),
            )
        )
    return results


@router.get(
    "/threat-intel/lookup/{indicator_type}/{indicator_value}",
    response_model=List[ThreatIndicator],
    name="threat_intel:lookup",
)
def lookup_indicator(
    indicator_type: str,
    indicator_value: str,
    db: Session = Depends(get_session),
) -> List[ThreatIndicator]:
    """Look up a specific indicator value across all sources."""
    rows = (
        db.query(ThreatIntelRef)
        .filter(
            ThreatIntelRef.indicator_type == indicator_type,
            ThreatIntelRef.indicator_value == indicator_value,
        )
        .order_by(ThreatIntelRef.fetched_at.desc())
        .all()
    )
    return [
        ThreatIndicator(
            id=r.id,
            indicator_type=r.indicator_type,
            indicator_value=r.indicator_value,
            pulse_id=r.pulse_id,
            pulse_name=r.pulse_name,
            source=r.source,
            source_url=r.source_url,
            fetched_at=r.fetched_at.isoformat() if r.fetched_at else None,
        )
        for r in rows
    ]


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
        db.execute(
            text(
                """
            INSERT INTO mcp_server_registry (server_id, name, registry_source, risk_tier, url)
            VALUES
                ('srv1','Test Server 1','github','HIGH','https://github.com/srv1'),
                ('srv2','Test Server 2','npm','MEDIUM','https://npmjs.com/srv2'),
                ('srv3','Clean Server','github','LOW','https://github.com/srv3');
            """
            )
        )
        db.execute(
            text(
                """
            INSERT INTO threat_intel_refs
            (indicator_type, indicator_value, pulse_id, pulse_name, pulse_created,
             is_aggregator, source, source_url, fetched_at)
            VALUES
                ('ip','1.2.3.4','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-01'),
                ('domain','evil.com','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-01'),
                ('server','srv1','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-02'),
                ('ip','5.6.7.8','pulse2','Suspicious IPs','2023-02-01',0,'alienvault','https://av/2','2023-06-03'),
                ('server','srv2','pulse2','Suspicious IPs','2023-02-01',0,'alienvault','https://av/2','2023-06-03'),
                ('url','https://bad.net/payload','pulse3','Malware URL','2023-03-01',1,'vt','https://vt/3','2023-06-04');
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

    # Test summary endpoint
    resp = _c.get("/api/threat-intel/summary")
    assert resp.status_code == 200, f"Summary failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["total_indicators"] == 6, f"Expected 6 indicators, got {data['total_indicators']}"
    assert data["by_type"]["ip"] == 2, f"Expected 2 ip indicators, got {data['by_type']}"
    assert data["pulses"] == 3, f"Expected 3 pulses, got {data['pulses']}"
    assert data["aggregators"] == 3, f"Expected 3 aggregators, got {data['aggregators']}"

    # Test list indicators
    resp = _c.get("/api/threat-intel/indicators?limit=10")
    assert resp.status_code == 200
    assert len(resp.json()) == 6

    # Test filter by type
    resp = _c.get("/api/threat-intel/indicators?indicator_type=ip")
    assert resp.status_code == 200
    assert len(resp.json()) == 2

    # Test filter by source
    resp = _c.get("/api/threat-intel/indicators?source=otx")
    assert resp.status_code == 200
    assert len(resp.json()) == 3

    # Test pulse detail
    resp = _c.get("/api/threat-intel/pulses/pulse1")
    assert resp.status_code == 200, f"Pulse detail failed: {resp.status_code} {resp.text}"
    pulse = resp.json()
    assert pulse["pulse_id"] == "pulse1"
    assert pulse["indicator_count"] == 3, f"Expected 3 indicators, got {pulse['indicator_count']}"
    assert pulse["is_aggregator"] is True

    # Test pulse 404
    resp = _c.get("/api/threat-intel/pulses/nonexistent")
    assert resp.status_code == 404

    # Test server exposure
    resp = _c.get("/api/threat-intel/servers/exposure")
    assert resp.status_code == 200
    servers = resp.json()
    assert len(servers) == 2, f"Expected 2 servers with exposure, got {len(servers)}"
    srv1 = next(s for s in servers if s["server_id"] == "srv1")
    assert srv1["indicator_count"] == 1
    assert srv1["pulse_count"] == 1

    # Test indicator lookup
    resp = _c.get("/api/threat-intel/lookup/ip/1.2.3.4")
    assert resp.status_code == 200
    results = resp.json()
    assert len(results) == 1
    assert results[0]["source"] == "otx"

    # Test lookup with no results
    resp = _c.get("/api/threat-intel/lookup/ip/99.99.99.99")
    assert resp.status_code == 200
    assert len(resp.json()) == 0

    print("PASS")
