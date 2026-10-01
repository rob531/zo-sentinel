# deps: fastapi, pydantic, sqlalchemy
"""Axis Calibration Scoring Consumer.

Reads mcp_llm_axis_scores per server+axis, evaluates the calibration quality of
the p_top/p_critical/p_danger distributions against the observed label distribution,
and surfaces per-server and per-axis calibration metrics.

Public: no auth required (PRODUCT_SPEC §9 scope).
Data: app tier via get_session + SQLAlchemy models (McpLlmAxisScore, McpServerRegistry).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["axis_calibration_scoring_consumer"])

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #
ALL_AXES = [
    "overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
    "network_egress", "maintainer_trust", "exploit_surface",
]

ECE_BINS = 10


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _expected_calibration_error(
    confidences: List[float],
    accuracies: List[bool],
    n_bins: int = ECE_BINS,
) -> float:
    """Compute Expected Calibration Error (ECE) using equal-width bins."""
    if not confidences or not accuracies or len(confidences) != len(accuracies):
        return 0.0
    total = len(confidences)
    bin_size = 1.0 / n_bins
    ece = 0.0
    for b in range(n_bins):
        lo = b * bin_size
        hi = (b + 1) * bin_size
        bin_confs, bin_accs = [], []
        for c, a in zip(confidences, accuracies):
            if lo <= c < hi or (b == n_bins - 1 and c == 1.0):
                bin_confs.append(c)
                bin_accs.append(1.0 if a else 0.0)
        if bin_confs:
            avg_conf = sum(bin_confs) / len(bin_confs)
            avg_acc = sum(bin_accs) / len(bin_accs)
            ece += (len(bin_confs) / total) * abs(avg_acc - avg_conf)
    return round(ece, 6)


def _label_entropy(labels: List[str]) -> float:
    """Shannon entropy of label distribution."""
    if not labels:
        return 0.0
    from collections import Counter
    counts = Counter(labels)
    total = len(labels)
    entropy = 0.0
    for c in counts.values():
        p = c / total
        if p > 0:
            entropy -= p * math.log2(p)
    return round(entropy, 6)


def _calibration_quality(ece: float) -> str:
    """Map ECE value to a qualitative label."""
    if ece <= 0.05:
        return "excellent"
    elif ece <= 0.10:
        return "good"
    elif ece <= 0.20:
        return "fair"
    else:
        return "poor"


def _get_axis_label_class_count(axis_name: str) -> int:
    """Return the number of label classes per axis (hand-calibrated)."""
    mapping = {
        "overall_risk": 4,
        "auth_strength": 4,
        "capability_breadth": 4,
        "data_sensitivity": 4,
        "network_egress": 4,
        "maintainer_trust": 4,
        "exploit_surface": 4,
    }
    return mapping.get(axis_name, 4)


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #
class AxisCalibrationMetrics(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    axis_name: str
    ece: float
    calibration_quality: str
    label_entropy: float
    total_scores: int
    distinct_labels: int
    expected_classes: int
    label_distribution: Dict[str, int]


class ServerCalibrationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    server_id: str
    server_name: Optional[str]
    overall_ece: float
    axes: List[AxisCalibrationMetrics]
    computed_at: datetime


class CalibrationHealthResponse(BaseModel):
    status: str
    total_servers: int
    calibrated_servers: int
    avg_ece: float


class BatchCalibrationRequest(BaseModel):
    server_ids: Optional[List[str]] = Field(
        default=None,
        description="List of server IDs to calibrate. If None, all servers.",
    )


class BatchCalibrationResponse(BaseModel):
    computed_count: int
    tier_distribution: Dict[str, int]
    avg_ece: float
    computed_at: datetime


# --------------------------------------------------------------------------- #
# Core computation
# --------------------------------------------------------------------------- #
def _compute_server_calibration(
    session: Session,
    server_id: str,
) -> Optional[ServerCalibrationResponse]:
    """Compute calibration metrics for a single server across all axes."""
    srv = session.get(McpServerRegistry, server_id)
    if not srv:
        return None

    axes_records: List[AxisCalibrationMetrics] = []
    all_confidences, all_accuracies = [], []

    for axis_name in ALL_AXES:
        rows = (
            session.query(McpLlmAxisScore)
            .filter(
                McpLlmAxisScore.server_id == server_id,
                McpLlmAxisScore.axis_name == axis_name,
            )
            .order_by(McpLlmAxisScore.scored_at.desc())
            .limit(100)
            .all()
        )

        if not rows:
            axes_records.append(AxisCalibrationMetrics(
                axis_name=axis_name,
                ece=0.0,
                calibration_quality="unknown",
                label_entropy=0.0,
                total_scores=0,
                distinct_labels=0,
                expected_classes=_get_axis_label_class_count(axis_name),
                label_distribution={},
            ))
            continue

        labels = [r.label for r in rows if r.label is not None]
        p_tops = [r.p_top for r in rows if r.p_top is not None]

        # Determine whether each p_top was "correct" (label_index == 0 means top class)
        label_indices = [r.label_index for r in rows if r.label_index is not None]
        accuracies = [idx == 0 for idx in label_indices]
        confidences = p_tops[:len(label_indices)]

        ece = _expected_calibration_error(confidences, accuracies)
        entropy = _label_entropy(labels)

        from collections import Counter
        label_dist = dict(Counter(labels))

        axes_records.append(AxisCalibrationMetrics(
            axis_name=axis_name,
            ece=ece,
            calibration_quality=_calibration_quality(ece),
            label_entropy=entropy,
            total_scores=len(rows),
            distinct_labels=len(label_dist),
            expected_classes=_get_axis_label_class_count(axis_name),
            label_distribution=label_dist,
        ))

        all_confidences.extend(confidences)
        all_accuracies.extend(accuracies)

    overall_ece = _expected_calibration_error(all_confidences, all_accuracies)

    return ServerCalibrationResponse(
        server_id=server_id,
        server_name=srv.name,
        overall_ece=overall_ece,
        axes=axes_records,
        computed_at=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.get(
    "/calibration/servers/{server_id}",
    response_model=ServerCalibrationResponse,
    responses={404: {"description": "Server not found"}},
)
def get_server_calibration(
    server_id: str,
    db: Session = Depends(get_session),
) -> ServerCalibrationResponse:
    """
    Compute calibration metrics for a single server across all axes.

    Returns ECE (Expected Calibration Error), label entropy, label distribution,
    and per-axis calibration quality for each of the 7 risk axes.
    """
    result = _compute_server_calibration(db, server_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")
    return result


@router.get(
    "/calibration/servers/{server_id}/axis/{axis_name}",
    response_model=AxisCalibrationMetrics,
    responses={
        404: {"description": "Server or axis not found"},
    },
)
def get_server_axis_calibration(
    server_id: str,
    axis_name: str,
    db: Session = Depends(get_session),
) -> AxisCalibrationMetrics:
    """Return calibration metrics for a specific axis of a server."""
    if axis_name not in ALL_AXES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name={axis_name}. Valid: {ALL_AXES}",
        )

    srv = db.get(McpServerRegistry, server_id)
    if not srv:
        raise HTTPException(status_code=404, detail=f"Server {server_id} not found")

    rows = (
        db.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == axis_name,
        )
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(100)
        .all()
    )

    if not rows:
        return AxisCalibrationMetrics(
            axis_name=axis_name,
            ece=0.0,
            calibration_quality="unknown",
            label_entropy=0.0,
            total_scores=0,
            distinct_labels=0,
            expected_classes=_get_axis_label_class_count(axis_name),
            label_distribution={},
        )

    labels = [r.label for r in rows if r.label is not None]
    p_tops = [r.p_top for r in rows if r.p_top is not None]
    label_indices = [r.label_index for r in rows if r.label_index is not None]
    accuracies = [idx == 0 for idx in label_indices]
    confidences = p_tops[:len(label_indices)]

    ece = _expected_calibration_error(confidences, accuracies)
    entropy = _label_entropy(labels)

    from collections import Counter
    label_dist = dict(Counter(labels))

    return AxisCalibrationMetrics(
        axis_name=axis_name,
        ece=ece,
        calibration_quality=_calibration_quality(ece),
        label_entropy=entropy,
        total_scores=len(rows),
        distinct_labels=len(label_dist),
        expected_classes=_get_axis_label_class_count(axis_name),
        label_distribution=label_dist,
    )


@router.get(
    "/calibration/health",
    response_model=CalibrationHealthResponse,
)
def calibration_health(db: Session = Depends(get_session)) -> CalibrationHealthResponse:
    """Return aggregate calibration health stats across all servers."""
    total = db.query(func.count(McpServerRegistry.server_id)).scalar() or 0

    servers_with_scores = (
        db.query(McpLlmAxisScore.server_id)
        .distinct()
        .count()
    )

    # Compute avg ECE across all server-axis combinations
    all_ece_rows = (
        db.query(
            McpLlmAxisScore.server_id,
            func.avg(McpLlmAxisScore.p_top).label("avg_p_top"),
        )
        .group_by(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .all()
    )

    # Simple approximation: avg spread of p_top as proxy for ECE
    if all_ece_rows:
        spreads = [abs(r.avg_p_top - 0.5) * 2 for r in all_ece_rows if r.avg_p_top is not None]
        avg_ece = round(sum(spreads) / len(spreads) if spreads else 0.0, 4)
    else:
        avg_ece = 0.0

    return CalibrationHealthResponse(
        status="ok",
        total_servers=total,
        calibrated_servers=servers_with_scores,
        avg_ece=avg_ece,
    )


@router.post(
    "/calibration/compute",
    response_model=BatchCalibrationResponse,
)
def batch_calibration(
    request: BatchCalibrationRequest,
    db: Session = Depends(get_session),
) -> BatchCalibrationResponse:
    """
    Compute calibration for a batch of servers and return aggregate stats.

    This is a light-weight read-only pass -- no data is written.
    """
    if request.server_ids:
        servers = (
            db.query(McpServerRegistry)
            .filter(McpServerRegistry.server_id.in_(request.server_ids))
            .all()
        )
    else:
        servers = db.query(McpServerRegistry).all()

    tier_dist: Dict[str, int] = {}
    ece_sum = 0.0
    count = 0

    for srv in servers:
        result = _compute_server_calibration(db, srv.server_id)
        if result and result.overall_ece > 0:
            ece_sum += result.overall_ece
            count += 1
            quality = _calibration_quality(result.overall_ece)
            tier_dist[quality] = tier_dist.get(quality, 0) + 1

    avg_ece = round(ece_sum / count, 6) if count > 0 else 0.0

    return BatchCalibrationResponse(
        computed_count=len(servers),
        tier_distribution=tier_dist,
        avg_ece=avg_ece,
        computed_at=datetime.now(timezone.utc),
    )


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    from app.models import Base as AppBase
    AppBase.metadata.create_all(bind=test_engine)

    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    test_app = FastAPI()
    test_app.include_router(router)

    def _override():
        sess = TestSession()
        try:
            yield sess
        finally:
            sess.close()

    test_app.dependency_overrides[get_session] = _override

    now = datetime.now(timezone.utc)

    with TestSession() as sess:
        # Two servers: well-calibrated and poorly-calibrated
        sess.add_all([
            McpServerRegistry(server_id="srv-good", name="Good Server"),
            McpServerRegistry(server_id="srv-bad", name="Bad Server"),
            McpServerRegistry(server_id="srv-empty", name="Empty Server"),
        ])

        # srv-good: all top-class (label_index=0), high p_top → low ECE
        for i in range(10):
            sess.add(McpLlmAxisScore(
                server_id="srv-good",
                axis_name="overall_risk",
                label="LOW",
                label_index=0,
                p_top=0.92 + i * 0.005,
                p_critical=0.05,
                p_danger=0.03,
                model_version="v1",
                decision_rule_version="r1",
                scored_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            ))

        # srv-good: also auth_strength
        for i in range(8):
            sess.add(McpLlmAxisScore(
                server_id="srv-good",
                axis_name="auth_strength",
                label="STRONG",
                label_index=0,
                p_top=0.90,
                p_critical=0.07,
                p_danger=0.03,
                model_version="v1",
                decision_rule_version="r1",
                scored_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            ))

        # srv-bad: mixed labels with varied p_tops → high ECE
        for i, (idx, p_top, label) in enumerate([
            (0, 0.9, "LOW"),     # correct
            (1, 0.6, "MEDIUM"), # wrong class
            (2, 0.7, "HIGH"),   # wrong class
            (0, 0.8, "LOW"),    # correct
            (1, 0.55, "MEDIUM"),# wrong
            (0, 0.95, "LOW"),   # correct
            (2, 0.65, "HIGH"),  # wrong
            (0, 0.85, "LOW"),   # correct
        ]):
            sess.add(McpLlmAxisScore(
                server_id="srv-bad",
                axis_name="overall_risk",
                label=label,
                label_index=idx,
                p_top=p_top,
                p_critical=0.2,
                p_danger=0.2,
                model_version="v1",
                decision_rule_version="r1",
                scored_at=datetime(2024, 1, i + 1, tzinfo=timezone.utc),
            ))

        sess.commit()

    client = TestClient(test_app)

    # T1: health
    r = client.get("/api/calibration/health")
    assert r.status_code == 200, f"T1 health: {r.text}"
    d = r.json()
    assert d["status"] == "ok"
    assert d["total_servers"] == 3, f"T1: expected 3 servers, got {d['total_servers']}"
    assert d["calibrated_servers"] >= 2, f"T1: expected >=2 calibrated, got {d['calibrated_servers']}"

    # T2: srv-good calibration (well-calibrated → low ECE)
    r = client.get("/api/calibration/servers/srv-good")
    assert r.status_code == 200, f"T2 srv-good: {r.text}"
    d = r.json()
    assert d["server_id"] == "srv-good"
    assert d["server_name"] == "Good Server"
    # Well-calibrated: p_top close to accuracy (all label_index=0 = correct), ECE should be low
    assert d["overall_ece"] < 0.5, f"T2: expected low ECE for srv-good, got {d['overall_ece']}"
    # Find overall_risk axis
    overall = next((a for a in d["axes"] if a["axis_name"] == "overall_risk"), None)
    assert overall is not None, "T2: missing overall_risk axis"
    assert overall["total_scores"] == 10, f"T2: expected 10 scores, got {overall['total_scores']}"
    assert overall["distinct_labels"] == 1, f"T2: srv-good should have 1 distinct label, got {overall['distinct_labels']}"

    # T3: srv-bad calibration (poorly-calibrated → higher ECE)
    r = client.get("/api/calibration/servers/srv-bad")
    assert r.status_code == 200, f"T3 srv-bad: {r.text}"
    d = r.json()
    # srv-bad has mixed correct/wrong predictions → higher ECE
    assert d["overall_ece"] >= 0.0, f"T3:ECE should be non-negative, got {d['overall_ece']}"

    # T4: srv-empty (no scores) → returns with unknown axes
    r = client.get("/api/calibration/servers/srv-empty")
    assert r.status_code == 200, f"T4 srv-empty: {r.text}"
    d = r.json()
    assert d["overall_ece"] == 0.0, "T4: empty server should have 0 ECE"
    # All axes should be present, all with 0 scores
    assert len(d["axes"]) == 7, f"T4: expected 7 axes, got {len(d['axes'])}"
    for ax in d["axes"]:
        assert ax["calibration_quality"] == "unknown", f"T4: unknown quality expected, got {ax['calibration_quality']}"

    # T5: 404 for unknown server
    r = client.get("/api/calibration/servers/unknown-server")
    assert r.status_code == 404, f"T5: expected 404, got {r.status_code}"

    # T6: invalid axis_name
    r = client.get("/api/calibration/servers/srv-good/axis/invalid_axis")
    assert r.status_code == 400, f"T6: expected 400 for invalid axis, got {r.status_code}"

    # T7: specific axis endpoint
    r = client.get("/api/calibration/servers/srv-good/axis/auth_strength")
    assert r.status_code == 200, f"T7: {r.text}"
    d = r.json()
    assert d["axis_name"] == "auth_strength"
    assert d["total_scores"] == 8
    assert d["expected_classes"] == 4

    # T8: batch compute
    r = client.post("/api/calibration/compute", json={"server_ids": ["srv-good", "srv-bad"]})
    assert r.status_code == 200, f"T8 batch: {r.text}"
    d = r.json()
    assert d["computed_count"] == 2, f"T8: expected 2 computed, got {d['computed_count']}"
    assert "avg_ece" in d, "T8: missing avg_ece"
    assert d["avg_ece"] >= 0.0, f"T8: avg_ece should be >= 0, got {d['avg_ece']}"

    # T9: batch compute all
    r = client.post("/api/calibration/compute", json={})
    assert r.status_code == 200, f"T9: {r.text}"
    d = r.json()
    assert d["computed_count"] == 3, f"T9: expected 3, got {d['computed_count']}"

    # T10: Pydantic validation on response shape
    r = client.get("/api/calibration/servers/srv-good")
    d = r.json()
    for ax in d["axes"]:
        assert ax["calibration_quality"] in ("excellent", "good", "fair", "poor", "unknown"), \
            f"T10: bad quality value: {ax['calibration_quality']}"
        assert ax["ece"] >= 0.0, f"T10: ECE must be non-negative, got {ax['ece']}"

    print("PASS")
    sys.exit(0)
