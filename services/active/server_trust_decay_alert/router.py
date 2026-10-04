# deps: fastapi, pydantic, sqlalchemy
"""server_trust_decay_alert — alert on servers showing trust regression.

Detects servers whose maintainer_trust axis has degraded vs. a prior baseline
window, computes decay severity, and surfaces actionable alerts.

Auth: public.
Data: app Postgres via get_session + McpLlmAxisScore + McpServerRegistry.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base as AppBase, McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_trust_decay_alert"])


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
# Ordinal mapping for maintainer_trust labels
_TRUST_ORDINAL: dict[str, int] = {
    "UNKNOWN": 0,
    "NONE": 1,
    "LOW": 2,
    "MEDIUM": 3,
    "HIGH": 4,
    "ESTABLISHED": 5,
    "VERIFIED": 6,
}
_NEUTRAL_ORDINAL = 2

# Minimum ordinal drop to flag as decay
_MIN_DECAY_DELTA = 1


def _ordinal(label: str | None) -> int:
    if label is None:
        return _NEUTRAL_ORDINAL
    return _TRUST_ORDINAL.get(label.upper(), _NEUTRAL_ORDINAL)


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AxisSnapshotOut(BaseModel):
    axis_name: str
    label: str | None
    p_top: float | None
    p_critical: float | None
    p_danger: float | None
    model_config = ConfigDict(from_attributes=True)


class TrustDecayAlert(BaseModel):
    server_id: str
    server_name: str | None
    baseline_label: str | None
    current_label: str | None
    baseline_ordinal: int
    current_ordinal: int
    ordinal_drop: int
    decay_pct: float
    severity: str
    baseline_snapshot: AxisSnapshotOut | None
    current_snapshot: AxisSnapshotOut | None
    scored_at: str
    lookback_days: int
    model_config = ConfigDict(from_attributes=True)


class TrustDecayAlertResponse(BaseModel):
    period_days: int
    alert_count: int
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int
    fleet_severity: str
    alerts: list[TrustDecayAlert]
    as_of: str


class TrustDecaySummary(BaseModel):
    total_servers_scored: int
    servers_in_decay: int
    decay_rate_pct: float
    critical_count: int
    high_count: int
    medium_count: int
    low_count: int


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _decay_severity(ordinal_drop: int, baseline_ordinal: int) -> str:
    if baseline_ordinal <= 0:
        baseline_ordinal = 1
    pct = ordinal_drop / baseline_ordinal
    if ordinal_drop >= 4 or pct >= 0.80:
        return "critical"
    if ordinal_drop >= 3 or pct >= 0.60:
        return "high"
    if ordinal_drop >= 2 or pct >= 0.40:
        return "medium"
    return "low"


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get(
    "/alerts/trust-decay",
    response_model=TrustDecayAlertResponse,
    summary="Alert on servers showing trust regression",
)
def get_trust_decay_alerts(
    period_days: int = Query(default=30, ge=7, le=365),
    severity_threshold: str = Query(default="low", regex="^(low|medium|high|critical)$"),
    db: Session = Depends(get_session),
) -> TrustDecayAlertResponse:
    """
    Return all servers whose maintainer_trust label has degraded relative to
    the most-recent snapshot before the lookback cutoff.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=period_days)
    severity_order = ["low", "medium", "high", "critical"]
    threshold_idx = severity_order.index(severity_threshold)

    # All distinct servers that have a maintainer_trust score in the window
    scored_sub = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    latest_rows = (
        db.query(McpLlmAxisScore)
        .join(
            scored_sub,
            (McpLlmAxisScore.server_id == scored_sub.c.server_id)
            & (McpLlmAxisScore.scored_at == scored_sub.c.latest_at),
        )
        .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
        .all()
    )

    if not latest_rows:
        return TrustDecayAlertResponse(
            period_days=period_days,
            alert_count=0,
            critical_count=0,
            high_count=0,
            medium_count=0,
            low_count=0,
            fleet_severity="none",
            alerts=[],
            as_of=now.isoformat(),
        )

    # Fetch server names
    server_ids = [r.server_id for r in latest_rows]
    name_rows = (
        db.query(McpServerRegistry.server_id, McpServerRegistry.name)
        .filter(McpServerRegistry.server_id.in_(server_ids))
        .all()
    )
    name_map = {r.server_id: r.name for r in name_rows}

    # For each server: find the baseline snapshot (most-recent before cutoff)
    alerts: list[TrustDecayAlert] = []
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    for latest in latest_rows:
        prev_rows = (
            db.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == latest.server_id)
            .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
            .filter(McpLlmAxisScore.scored_at < cutoff)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
            .all()
        )

        if not prev_rows:
            continue

        baseline = prev_rows[0]
        cur_ord = _ordinal(latest.label)
        base_ord = _ordinal(baseline.label)
        drop = base_ord - cur_ord

        if drop < _MIN_DECAY_DELTA:
            continue

        pct = (drop / base_ord) if base_ord > 0 else 1.0
        severity = _decay_severity(drop, base_ord)

        if severity_order.index(severity) < threshold_idx:
            continue

        counts[severity] += 1
        alerts.append(
            TrustDecayAlert(
                server_id=latest.server_id,
                server_name=name_map.get(latest.server_id),
                baseline_label=baseline.label,
                current_label=latest.label,
                baseline_ordinal=base_ord,
                current_ordinal=cur_ord,
                ordinal_drop=drop,
                decay_pct=round(pct, 4),
                severity=severity,
                baseline_snapshot=AxisSnapshotOut(
                    axis_name=baseline.axis_name,
                    label=baseline.label,
                    p_top=baseline.p_top,
                    p_critical=baseline.p_critical,
                    p_danger=baseline.p_danger,
                ),
                current_snapshot=AxisSnapshotOut(
                    axis_name=latest.axis_name,
                    label=latest.label,
                    p_top=latest.p_top,
                    p_critical=latest.p_critical,
                    p_danger=latest.p_danger,
                ),
                scored_at=latest.scored_at.isoformat() if latest.scored_at else "",
                lookback_days=period_days,
            )
        )

    # Sort alerts: worst decay first
    alerts.sort(key=lambda a: (-a.ordinal_drop, severity_order.index(a.severity)))

    fleet_severity = "none"
    if counts["critical"] > 0:
        fleet_severity = "critical"
    elif counts["high"] > 0:
        fleet_severity = "high"
    elif counts["medium"] > 0:
        fleet_severity = "medium"
    elif counts["low"] > 0:
        fleet_severity = "low"

    return TrustDecayAlertResponse(
        period_days=period_days,
        alert_count=len(alerts),
        critical_count=counts["critical"],
        high_count=counts["high"],
        medium_count=counts["medium"],
        low_count=counts["low"],
        fleet_severity=fleet_severity,
        alerts=alerts,
        as_of=now.isoformat(),
    )


@router.get(
    "/alerts/trust-decay/{server_id}",
    response_model=TrustDecayAlert,
    summary="Trust decay detail for a single server",
)
def get_server_trust_decay(
    server_id: str,
    period_days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> TrustDecayAlert:
    """Return trust decay detail for a specific server."""
    srv = db.get(McpServerRegistry, server_id)
    if srv is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Server {server_id!r} not found")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=period_days)

    # Latest maintainer_trust score
    latest = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
        .order_by(McpLlmAxisScore.scored_at.desc())
        .first()
    )

    if not latest:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"No maintainer_trust scores found for server {server_id!r}",
        )

    prev_rows = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
        .filter(McpLlmAxisScore.scored_at < cutoff)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
        .all()
    )

    if not prev_rows:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=404,
            detail=f"No baseline score found for server {server_id!r} "
                   f"before {cutoff.isoformat()}",
        )

    baseline = prev_rows[0]
    cur_ord = _ordinal(latest.label)
    base_ord = _ordinal(baseline.label)
    drop = base_ord - cur_ord
    pct = (drop / base_ord) if base_ord > 0 else 1.0
    severity = _decay_severity(drop, base_ord)

    return TrustDecayAlert(
        server_id=server_id,
        server_name=srv.name,
        baseline_label=baseline.label,
        current_label=latest.label,
        baseline_ordinal=base_ord,
        current_ordinal=cur_ord,
        ordinal_drop=drop,
        decay_pct=round(pct, 4),
        severity=severity,
        baseline_snapshot=AxisSnapshotOut(
            axis_name=baseline.axis_name,
            label=baseline.label,
            p_top=baseline.p_top,
            p_critical=baseline.p_critical,
            p_danger=baseline.p_danger,
        ),
        current_snapshot=AxisSnapshotOut(
            axis_name=latest.axis_name,
            label=latest.label,
            p_top=latest.p_top,
            p_critical=latest.p_critical,
            p_danger=latest.p_danger,
        ),
        scored_at=latest.scored_at.isoformat() if latest.scored_at else "",
        lookback_days=period_days,
    )


@router.get(
    "/alerts/trust-decay/summary",
    response_model=TrustDecaySummary,
    summary="Fleet-wide trust decay summary",
)
def get_trust_decay_summary(
    period_days: int = Query(default=30, ge=7, le=365),
    db: Session = Depends(get_session),
) -> TrustDecaySummary:
    """Return fleet-wide trust decay counts and rates."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=period_days)

    # Distinct servers scored in the window
    scored_count = (
        db.query(func.count(func.distinct(McpLlmAxisScore.server_id)))
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
        .scalar()
    ) or 0

    # All latest rows
    scored_sub = (
        db.query(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at).label("latest_at"),
        )
        .filter(McpLlmAxisScore.scored_at >= cutoff)
        .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
        .group_by(McpLlmAxisScore.server_id)
        .subquery()
    )

    latest_rows = (
        db.query(McpLlmAxisScore)
        .join(
            scored_sub,
            (McpLlmAxisScore.server_id == scored_sub.c.server_id)
            & (McpLlmAxisScore.scored_at == scored_sub.c.latest_at),
        )
        .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
        .all()
    )

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    servers_in_decay = 0

    for latest in latest_rows:
        prev_rows = (
            db.query(McpLlmAxisScore)
            .filter(McpLlmAxisScore.server_id == latest.server_id)
            .filter(McpLlmAxisScore.axis_name == "maintainer_trust")
            .filter(McpLlmAxisScore.scored_at < cutoff)
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(1)
            .all()
        )
        if not prev_rows:
            continue

        baseline = prev_rows[0]
        drop = _ordinal(baseline.label) - _ordinal(latest.label)
        if drop < _MIN_DECAY_DELTA:
            continue

        servers_in_decay += 1
        severity = _decay_severity(drop, _ordinal(baseline.label))
        counts[severity] += 1

    total = scored_count or 1
    return TrustDecaySummary(
        total_servers_scored=scored_count,
        servers_in_decay=servers_in_decay,
        decay_rate_pct=round(servers_in_decay / total * 100, 2),
        critical_count=counts["critical"],
        high_count=counts["high"],
        medium_count=counts["medium"],
        low_count=counts["low"],
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AppBase.metadata.create_all(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    test_app = FastAPI()
    test_app.include_router(router)

    def _override() -> TestSession:
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)
    t_old = now - timedelta(days=60)
    t_recent = now - timedelta(days=5)

    with TestSession() as sess:
        # Server A: VERIFIED(6) → NONE(1) = drop 5 = critical
        sess.add(McpServerRegistry(
            server_id="srv-decay-crit", name="Decay Critical", trust_score=50.0,
        ))
        sess.add(McpLlmAxisScore(
            id=1, server_id="srv-decay-crit", axis_name="maintainer_trust",
            label="VERIFIED", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=2, server_id="srv-decay-crit", axis_name="maintainer_trust",
            label="NONE", p_top=0.80, p_critical=0.15, p_danger=0.05,
            model_version="v1", scored_at=t_recent,
        ))

        # Server B: HIGH(4) → MEDIUM(3) = drop 1 = low
        sess.add(McpServerRegistry(
            server_id="srv-decay-low", name="Decay Low", trust_score=60.0,
        ))
        sess.add(McpLlmAxisScore(
            id=3, server_id="srv-decay-low", axis_name="maintainer_trust",
            label="HIGH", p_top=0.65, p_critical=0.25, p_danger=0.10,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=4, server_id="srv-decay-low", axis_name="maintainer_trust",
            label="MEDIUM", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_recent,
        ))

        # Server C: MEDIUM(3) → MEDIUM(3) = no decay (should not appear)
        sess.add(McpServerRegistry(
            server_id="srv-no-decay", name="No Decay", trust_score=70.0,
        ))
        sess.add(McpLlmAxisScore(
            id=5, server_id="srv-no-decay", axis_name="maintainer_trust",
            label="MEDIUM", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=6, server_id="srv-no-decay", axis_name="maintainer_trust",
            label="MEDIUM", p_top=0.55, p_critical=0.30, p_danger=0.15,
            model_version="v1", scored_at=t_recent,
        ))

        # Server D: ESTABLISHED(5) → LOW(2) = drop 3 = high
        sess.add(McpServerRegistry(
            server_id="srv-decay-high", name="Decay High", trust_score=40.0,
        ))
        sess.add(McpLlmAxisScore(
            id=7, server_id="srv-decay-high", axis_name="maintainer_trust",
            label="ESTABLISHED", p_top=0.50, p_critical=0.30, p_danger=0.20,
            model_version="v1", scored_at=t_old,
        ))
        sess.add(McpLlmAxisScore(
            id=8, server_id="srv-decay-high", axis_name="maintainer_trust",
            label="LOW", p_top=0.70, p_critical=0.20, p_danger=0.10,
            model_version="v1", scored_at=t_recent,
        ))

        # Server E: only current score, no baseline (should be excluded)
        sess.add(McpServerRegistry(
            server_id="srv-no-baseline", name="No Baseline", trust_score=55.0,
        ))
        sess.add(McpLlmAxisScore(
            id=9, server_id="srv-no-baseline", axis_name="maintainer_trust",
            label="LOW", p_top=0.70, p_critical=0.20, p_danger=0.10,
            model_version="v1", scored_at=t_recent,
        ))

        sess.commit()

    client = TestClient(test_app)

    # Test 1: alerts endpoint
    r = client.get("/api/alerts/trust-decay", params={"period_days": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alert_count"] == 3, f"expected 3 alerts, got {body['alert_count']}: {body}"
    assert body["critical_count"] == 1, f"expected 1 critical, got {body['critical_count']}"
    assert body["high_count"] == 1, f"expected 1 high, got {body['high_count']}"
    assert body["low_count"] == 1, f"expected 1 low, got {body['low_count']}"
    assert body["fleet_severity"] == "critical", f"expected critical, got {body['fleet_severity']}"

    # srv-decay-crit should be first (worst decay)
    assert body["alerts"][0]["server_id"] == "srv-decay-crit"
    assert body["alerts"][0]["ordinal_drop"] == 5
    assert body["alerts"][0]["severity"] == "critical"
    assert body["alerts"][0]["baseline_label"] == "VERIFIED"
    assert body["alerts"][0]["current_label"] == "NONE"

    # srv-no-decay should NOT be in alerts
    ids = [a["server_id"] for a in body["alerts"]]
    assert "srv-no-decay" not in ids, "srv-no-decay should not appear"
    assert "srv-no-baseline" not in ids, "srv-no-baseline should not appear"

    # Test 2: severity filter
    r2 = client.get("/api/alerts/trust-decay", params={"period_days": 30, "severity_threshold": "high"})
    assert r2.status_code == 200
    body2 = r2.json()
    assert body2["alert_count"] == 2, f"expected 2 alerts at high threshold, got {body2['alert_count']}"
    sev_ids = [a["server_id"] for a in body2["alerts"]]
    assert "srv-decay-low" not in sev_ids, "low severity should be filtered"

    # Test 3: single server detail
    r3 = client.get("/api/alerts/trust-decay/srv-decay-crit", params={"period_days": 30})
    assert r3.status_code == 200, r3.text
    d = r3.json()
    assert d["server_id"] == "srv-decay-crit"
    assert d["ordinal_drop"] == 5
    assert d["severity"] == "critical"
    assert d["baseline_ordinal"] == 6
    assert d["current_ordinal"] == 1
    assert d["baseline_label"] == "VERIFIED"
    assert d["current_label"] == "NONE"
    assert d["server_name"] == "Decay Critical"

    # Test 4: 404 for unknown server
    r4 = client.get("/api/alerts/trust-decay/nonexistent", params={"period_days": 30})
    assert r4.status_code == 404, f"expected 404, got {r4.status_code}"

    # Test 5: server with no baseline
    r5 = client.get("/api/alerts/trust-decay/srv-no-baseline", params={"period_days": 30})
    assert r5.status_code == 404, f"srv-no-baseline should 404, got {r5.status_code}"

    # Test 6: summary endpoint
    r6 = client.get("/api/alerts/trust-decay/summary", params={"period_days": 30})
    assert r6.status_code == 200, r6.text
    s = r6.json()
    assert s["total_servers_scored"] == 5, f"expected 5 scored, got {s['total_servers_scored']}"
    assert s["servers_in_decay"] == 3, f"expected 3 in decay, got {s['servers_in_decay']}"
    assert s["decay_rate_pct"] > 0, f"decay_rate_pct should be >0"
    assert s["critical_count"] == 1
    assert s["high_count"] == 1

    # Test 7: period_days=1 (no scores in window)
    r7 = client.get("/api/alerts/trust-decay", params={"period_days": 1})
    assert r7.status_code == 200
    body7 = r7.json()
    assert body7["alert_count"] == 0
    assert body7["fleet_severity"] == "none"

    print("PASS")
    sys.exit(0)
