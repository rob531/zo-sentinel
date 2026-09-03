# deps: fastapi, pydantic, sqlalchemy
"""Threat Intel Lookup API.

Searches threat intelligence reference records by indicator type/value,
lists references with filters, retrieves pulse-level indicator groups,
and returns aggregate summary statistics.
Reads from app Postgres (ThreatIntelRef) via get_session.
auth=public -- no JWT/session guard needed.
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
from app.models import Base, ThreatIntelRef

router = APIRouter(prefix="/api", tags=["threat_intel_lookup_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class ThreatIntelMatch(BaseModel):
    pulse_id: str
    pulse_name: Optional[str] = None
    pulse_created: Optional[str] = None
    is_aggregator: bool = False
    source: str
    source_url: Optional[str] = None
    fetched_at: Optional[str] = None


class ThreatLookupResponse(BaseModel):
    indicator_type: str
    indicator_value: str
    total: int
    matches: List[ThreatIntelMatch]


class ThreatRefRecord(BaseModel):
    id: int
    indicator_type: str
    indicator_value: str
    pulse_id: str
    pulse_name: Optional[str] = None
    pulse_created: Optional[str] = None
    is_aggregator: bool = False
    source: str
    source_url: Optional[str] = None
    fetched_at: Optional[str] = None


class ThreatRefListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    records: List[ThreatRefRecord]


class PulseIndicatorGroup(BaseModel):
    pulse_id: str
    pulse_name: Optional[str] = None
    pulse_created: Optional[str] = None
    is_aggregator: bool = False
    indicator_count: int
    indicators: List[ThreatRefRecord]


class ThreatSummaryResponse(BaseModel):
    total_refs: int
    by_type: Dict[str, int]
    by_source: Dict[str, int]
    unique_pulses: int
    aggregators: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/threat/lookup",
    response_model=ThreatLookupResponse,
    name="threat_intel_lookup_api:lookup",
)
def lookup_threat_indicator(
    indicator_type: str = Query(..., description="Indicator type (e.g. ip, domain, url, cve)"),
    indicator_value: str = Query(..., description="Indicator value to search"),
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ThreatLookupResponse:
    """Look up all threat intel references for a given indicator type and value."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total = (
        db.query(func.count(ThreatIntelRef.id))
        .filter(
            ThreatIntelRef.indicator_type == indicator_type,
            ThreatIntelRef.indicator_value == indicator_value,
            ThreatIntelRef.fetched_at >= cutoff,
        )
        .scalar()
        or 0
    )

    rows = (
        db.query(ThreatIntelRef)
        .filter(
            ThreatIntelRef.indicator_type == indicator_type,
            ThreatIntelRef.indicator_value == indicator_value,
            ThreatIntelRef.fetched_at >= cutoff,
        )
        .order_by(ThreatIntelRef.fetched_at.desc())
        .all()
    )

    return ThreatLookupResponse(
        indicator_type=indicator_type,
        indicator_value=indicator_value,
        total=total,
        matches=[
            ThreatIntelMatch(
                pulse_id=r.pulse_id,
                pulse_name=r.pulse_name,
                pulse_created=r.pulse_created.isoformat() if r.pulse_created else None,
                is_aggregator=r.is_aggregator or False,
                source=r.source or "UNKNOWN",
                source_url=r.source_url,
                fetched_at=r.fetched_at.isoformat() if r.fetched_at else None,
            )
            for r in rows
        ],
    )


@router.get(
    "/threat/refs",
    response_model=ThreatRefListResponse,
    name="threat_intel_lookup_api:list",
)
def list_threat_refs(
    indicator_type: Optional[str] = Query(None, description="Filter by indicator type"),
    source: Optional[str] = Query(None, description="Filter by source"),
    pulse_id: Optional[str] = Query(None, description="Filter by pulse ID"),
    days: int = Query(default=90, ge=1, le=365),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> ThreatRefListResponse:
    """List threat intel references with optional filters and pagination."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    q = db.query(ThreatIntelRef).filter(ThreatIntelRef.fetched_at >= cutoff)
    if indicator_type:
        q = q.filter(ThreatIntelRef.indicator_type == indicator_type)
    if source:
        q = q.filter(ThreatIntelRef.source == source)
    if pulse_id:
        q = q.filter(ThreatIntelRef.pulse_id == pulse_id)

    total = q.count()

    rows = q.order_by(ThreatIntelRef.fetched_at.desc()).offset(skip).limit(limit).all()

    return ThreatRefListResponse(
        total=total,
        skip=skip,
        limit=limit,
        records=[
            ThreatRefRecord(
                id=r.id,
                indicator_type=r.indicator_type,
                indicator_value=r.indicator_value,
                pulse_id=r.pulse_id,
                pulse_name=r.pulse_name,
                pulse_created=r.pulse_created.isoformat() if r.pulse_created else None,
                is_aggregator=r.is_aggregator or False,
                source=r.source or "UNKNOWN",
                source_url=r.source_url,
                fetched_at=r.fetched_at.isoformat() if r.fetched_at else None,
            )
            for r in rows
        ],
    )


@router.get(
    "/threat/pulses/{pulse_id}",
    response_model=PulseIndicatorGroup,
    name="threat_intel_lookup_api:pulse_indicators",
)
def get_pulse_indicators(
    pulse_id: str,
    db: Session = Depends(get_session),
) -> PulseIndicatorGroup:
    """Return all indicators belonging to a specific threat pulse."""
    rows = (
        db.query(ThreatIntelRef)
        .filter(ThreatIntelRef.pulse_id == pulse_id)
        .order_by(ThreatIntelRef.fetched_at.desc())
        .all()
    )

    if not rows:
        raise HTTPException(status_code=404, detail="Pulse not found")

    first = rows[0]
    return PulseIndicatorGroup(
        pulse_id=pulse_id,
        pulse_name=first.pulse_name,
        pulse_created=first.pulse_created.isoformat() if first.pulse_created else None,
        is_aggregator=first.is_aggregator or False,
        indicator_count=len(rows),
        indicators=[
            ThreatRefRecord(
                id=r.id,
                indicator_type=r.indicator_type,
                indicator_value=r.indicator_value,
                pulse_id=r.pulse_id,
                pulse_name=r.pulse_name,
                pulse_created=r.pulse_created.isoformat() if r.pulse_created else None,
                is_aggregator=r.is_aggregator or False,
                source=r.source or "UNKNOWN",
                source_url=r.source_url,
                fetched_at=r.fetched_at.isoformat() if r.fetched_at else None,
            )
            for r in rows
        ],
    )


@router.get(
    "/threat/summary",
    response_model=ThreatSummaryResponse,
    name="threat_intel_lookup_api:summary",
)
def get_threat_summary(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ThreatSummaryResponse:
    """Return aggregate statistics for threat intel references."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    total = (
        db.query(func.count(ThreatIntelRef.id))
        .filter(ThreatIntelRef.fetched_at >= cutoff)
        .scalar()
        or 0
    )

    by_type_rows = (
        db.query(
            ThreatIntelRef.indicator_type,
            func.count(ThreatIntelRef.id).label("cnt"),
        )
        .filter(ThreatIntelRef.fetched_at >= cutoff)
        .group_by(ThreatIntelRef.indicator_type)
        .all()
    )
    by_type = {r.indicator_type: r.cnt for r in by_type_rows}

    by_source_rows = (
        db.query(
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id).label("cnt"),
        )
        .filter(ThreatIntelRef.fetched_at >= cutoff)
        .group_by(ThreatIntelRef.source)
        .all()
    )
    by_source = {r.source or "UNKNOWN": r.cnt for r in by_source_rows}

    unique_pulses = (
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

    return ThreatSummaryResponse(
        total_refs=total,
        by_type=by_type,
        by_source=by_source,
        unique_pulses=unique_pulses,
        aggregators=aggregators,
    )


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
            INSERT INTO threat_intel_refs
            (indicator_type, indicator_value, pulse_id, pulse_name, pulse_created,
             is_aggregator, source, source_url, fetched_at)
            VALUES
                ('ip','1.2.3.4','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-01'),
                ('ip','1.2.3.4','pulse2','Suspicious IPs','2023-02-01',0,'alienvault','https://av/2','2023-06-02'),
                ('ip','5.6.7.8','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-03'),
                ('domain','evil.com','pulse1','Bad Actor List','2023-01-01',1,'otx','https://otx/1','2023-06-04'),
                ('cve','CVE-2023-1234','pulse3','Malware URL','2023-03-01',1,'vt','https://vt/3','2023-06-05'),
                ('hash','deadbeef1234','pulse4','Ransomware Hash','2023-04-01',0,'hybrid','https://hy/4','2022-01-01');
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

    # Test 1: lookup by indicator
    resp = _c.get("/api/threat/lookup?indicator_type=ip&indicator_value=1.2.3.4")
    assert resp.status_code == 200, f"Lookup failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["total"] == 2, f"Expected 2 refs for 1.2.3.4, got {data['total']}"
    assert len(data["matches"]) == 2
    assert data["matches"][0]["source"] in ("otx", "alienvault")

    # Test 2: list with no filters
    resp = _c.get("/api/threat/refs")
    assert resp.status_code == 200
    data = resp.json()
    # 90-day window excludes the old 2022 hash row
    assert data["total"] == 5, f"Expected 5 in 90-day window, got {data['total']}"
    assert len(data["records"]) == 5

    # Test 3: filter by type
    resp = _c.get("/api/threat/refs?indicator_type=domain")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["records"][0]["indicator_value"] == "evil.com"

    # Test 4: filter by source
    resp = _c.get("/api/threat/refs?source=otx")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3, f"Expected 3 otx refs, got {data['total']}"

    # Test 5: filter by pulse_id
    resp = _c.get("/api/threat/refs?pulse_id=pulse1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3, f"Expected 3 pulse1 refs, got {data['total']}"

    # Test 6: pulse indicators endpoint
    resp = _c.get("/api/threat/pulses/pulse1")
    assert resp.status_code == 200, f"Pulse indicators failed: {resp.status_code} {resp.text}"
    pulse = resp.json()
    assert pulse["pulse_id"] == "pulse1"
    assert pulse["indicator_count"] == 3, f"Expected 3 indicators in pulse1, got {pulse['indicator_count']}"
    assert pulse["is_aggregator"] is True

    # Test 7: pulse not found
    resp = _c.get("/api/threat/pulses/nonexistent")
    assert resp.status_code == 404

    # Test 8: summary endpoint
    resp = _c.get("/api/threat/summary")
    assert resp.status_code == 200, f"Summary failed: {resp.status_code} {resp.text}"
    summary = resp.json()
    assert summary["total_refs"] == 5, f"Expected 5 refs in 30-day window, got {summary['total_refs']}"
    assert "ip" in summary["by_type"], f"Missing 'ip' in by_type: {summary['by_type']}"
    assert summary["by_type"]["ip"] == 2, f"Expected 2 ip refs, got {summary['by_type']['ip']}"
    assert summary["unique_pulses"] == 3, f"Expected 3 pulses, got {summary['unique_pulses']}"
    assert summary["aggregators"] == 3, f"Expected 3 aggregator refs, got {summary['aggregators']}"

    # Test 9: pagination
    resp = _c.get("/api/threat/refs?skip=0&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["records"]) == 2
    assert data["skip"] == 0
    assert data["limit"] == 2

    # Test 10: wide time window includes old hash
    resp = _c.get("/api/threat/summary?days=400")
    assert resp.status_code == 200
    assert resp.json()["total_refs"] == 6, f"Expected 6 in 400-day window, got {resp.json()['total_refs']}"

    print("PASS")
