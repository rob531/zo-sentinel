# deps: fastapi, pydantic, sqlalchemy
"""Threat Intel Summary API.

Provides threat intelligence summary endpoints: aggregate statistics,
indicator lookup, and server-level threat exposure from threat_intel_refs.
Reads from app Postgres (ThreatIntelRef) via get_session.

Prefix: /api   (matches service.toml prefix="/api")
Auth: public.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure repo root is on sys.path so `from app.db` works in __main__ self-test
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, ThreatIntelRef

router = APIRouter(prefix="/api", tags=["threat_intel_summary_api"])


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class ThreatIntelRefRecord(BaseModel):
    id: int
    indicator_type: str
    indicator_value: str
    pulse_id: Optional[str] = None
    pulse_name: Optional[str] = None
    pulse_created: Optional[str] = None
    is_aggregator: bool = False
    source: Optional[str] = None
    source_url: Optional[str] = None
    fetched_at: Optional[str] = None


class ThreatIntelRefListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    records: List[ThreatIntelRefRecord]


class ThreatIntelSummaryResponse(BaseModel):
    total_refs: int
    by_type: Dict[str, int]
    by_source: Dict[str, int]
    unique_pulses: int
    aggregators: int


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #


@router.get(
    "/threat-intel-summary",
    response_model=ThreatIntelSummaryResponse,
    name="threat_intel_summary_api:summary",
)
def get_threat_intel_summary(
    days: int = Query(default=30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> ThreatIntelSummaryResponse:
    """Return aggregate statistics for threat intel references over the given window."""
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

    return ThreatIntelSummaryResponse(
        total_refs=total,
        by_type=by_type,
        by_source=by_source,
        unique_pulses=unique_pulses,
        aggregators=aggregators,
    )


@router.get(
    "/threat-intel-summary/lookup",
    response_model=ThreatIntelRefListResponse,
    name="threat_intel_summary_api:lookup",
)
def lookup_threat_intel(
    indicator_type: str = Query(..., description="Indicator type (e.g. ip, domain, cve)"),
    indicator_value: str = Query(..., description="Indicator value to search"),
    days: int = Query(default=90, ge=1, le=365),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> ThreatIntelRefListResponse:
    """Look up all threat intel references for a given indicator."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    q = db.query(ThreatIntelRef).filter(
        ThreatIntelRef.indicator_type == indicator_type,
        ThreatIntelRef.indicator_value == indicator_value,
        ThreatIntelRef.fetched_at >= cutoff,
    )

    total = q.count()

    rows = (
        q.order_by(ThreatIntelRef.fetched_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return ThreatIntelRefListResponse(
        total=total,
        skip=skip,
        limit=limit,
        records=[
            ThreatIntelRefRecord(
                id=r.id,
                indicator_type=r.indicator_type,
                indicator_value=r.indicator_value,
                pulse_id=r.pulse_id,
                pulse_name=r.pulse_name,
                pulse_created=r.pulse_created.isoformat() if r.pulse_created else None,
                is_aggregator=r.is_aggregator or False,
                source=r.source,
                source_url=r.source_url,
                fetched_at=r.fetched_at.isoformat() if r.fetched_at else None,
            )
            for r in rows
        ],
    )


@router.get(
    "/threat-intel-summary/pulse/{pulse_id}",
    response_model=ThreatIntelRefListResponse,
    name="threat_intel_summary_api:pulse",
)
def get_pulse_refs(
    pulse_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
) -> ThreatIntelRefListResponse:
    """Return all threat intel references belonging to a specific pulse."""
    q = db.query(ThreatIntelRef).filter(ThreatIntelRef.pulse_id == pulse_id)
    total = q.count()

    rows = (
        q.order_by(ThreatIntelRef.fetched_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    if total == 0:
        raise HTTPException(status_code=404, detail="Pulse not found")

    return ThreatIntelRefListResponse(
        total=total,
        skip=skip,
        limit=limit,
        records=[
            ThreatIntelRefRecord(
                id=r.id,
                indicator_type=r.indicator_type,
                indicator_value=r.indicator_value,
                pulse_id=r.pulse_id,
                pulse_name=r.pulse_name,
                pulse_created=r.pulse_created.isoformat() if r.pulse_created else None,
                is_aggregator=r.is_aggregator or False,
                source=r.source,
                source_url=r.source_url,
                fetched_at=r.fetched_at.isoformat() if r.fetched_at else None,
            )
            for r in rows
        ],
    )


@router.get(
    "/threat-intel-summary/sources",
    response_model=List[dict],
    name="threat_intel_summary_api:sources",
)
def list_sources(
    days: int = Query(default=90, ge=1, le=365),
    db: Session = Depends(get_session),
) -> List[dict]:
    """Return per-source aggregate counts of threat intel references."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.query(
            ThreatIntelRef.source,
            func.count(ThreatIntelRef.id).label("total"),
            func.count(func.distinct(ThreatIntelRef.pulse_id)).label("pulses"),
        )
        .filter(
            ThreatIntelRef.fetched_at >= cutoff,
            ThreatIntelRef.source.isnot(None),
        )
        .group_by(ThreatIntelRef.source)
        .order_by(func.count(ThreatIntelRef.id).desc())
        .all()
    )

    return [
        {
            "source": r.source or "UNKNOWN",
            "total_refs": r.total,
            "unique_pulses": r.pulses,
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

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
                ('url','https://bad.net/payload','pulse3','Malware URL','2023-03-01',1,'vt','https://vt/3','2023-06-05'),
                ('filehash','deadbeef1234','pulse4','Ransomware Hash','2023-04-01',0,'hybrid','https://hy/4','2022-01-01');
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

    # Test 1: summary endpoint
    resp = _c.get("/api/threat-intel-summary")
    assert resp.status_code == 200, f"Summary failed: {resp.status_code} {resp.text}"
    s = resp.json()
    assert s["total_refs"] == 5, f"Expected 5 in 30-day window, got {s['total_refs']}"
    assert "ip" in s["by_type"], f"Missing 'ip' in by_type: {s['by_type']}"
    assert s["by_type"]["ip"] == 2, f"Expected 2 ip refs, got {s['by_type']['ip']}"
    assert s["unique_pulses"] == 3, f"Expected 3 pulses, got {s['unique_pulses']}"
    assert s["aggregators"] == 3, f"Expected 3 aggregator refs, got {s['aggregators']}"

    # Test 2: lookup by indicator
    resp = _c.get("/api/threat-intel-summary/lookup?indicator_type=ip&indicator_value=1.2.3.4")
    assert resp.status_code == 200, f"Lookup failed: {resp.status_code} {resp.text}"
    data = resp.json()
    assert data["total"] == 2, f"Expected 2 refs for 1.2.3.4, got {data['total']}"
    assert len(data["records"]) == 2

    # Test 3: pulse refs
    resp = _c.get("/api/threat-intel-summary/pulse/pulse1")
    assert resp.status_code == 200, f"Pulse refs failed: {resp.status_code} {resp.text}"
    pulse = resp.json()
    assert pulse["total"] == 3, f"Expected 3 refs in pulse1, got {pulse['total']}"

    # Test 4: pulse not found
    resp = _c.get("/api/threat-intel-summary/pulse/nonexistent")
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"

    # Test 5: sources list
    resp = _c.get("/api/threat-intel-summary/sources")
    assert resp.status_code == 200
    srcs = resp.json()
    assert len(srcs) >= 1
    assert any(r["source"] == "otx" for r in srcs)

    # Test 6: pagination
    resp = _c.get("/api/threat-intel-summary/lookup?indicator_type=ip&indicator_value=1.2.3.4&skip=0&limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["records"]) == 1
    assert data["skip"] == 0
    assert data["limit"] == 1

    # Test 7: wide time window includes old filehash
    resp = _c.get("/api/threat-intel-summary?days=400")
    assert resp.status_code == 200
    assert resp.json()["total_refs"] == 6, f"Expected 6 in 400-day window, got {resp.json()['total_refs']}"

    print("PASS")
