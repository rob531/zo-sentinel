"""axis_label_taxonomy_report.py -- axis label taxonomy drift report generator.

Reads the axis label taxonomy from mcp_llm_axis_scores (label / label_index /
scored_at) via the REAL app data layer (app.db / app.models) and reports, per
valid axis: the labels observed, the canonical label indexes never observed
(missing_label_indexes) and the indexes that map to more than one label
(index_conflicts).

Read-only: no writes to any table, no network calls.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

# The only axis names reported on.
VALID_AXES: Tuple[str, ...] = (
    "overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
    "network_egress", "maintainer_trust", "exploit_surface",
)

# Canonical per-axis label ladders, VERBATIM from the cross-repo contract
# schemas/risk_axis_mapping_v1.json (mirrored in tools/rescore/score_validity.py).
# Index == position in the ladder, so the ladder length is the expected index set.
AXIS_LABEL_LADDERS: Dict[str, Tuple[str, ...]] = {
    "overall_risk": ("LOW", "MEDIUM", "HIGH", "CRITICAL"),
    "auth_strength": ("STRONG", "MODERATE", "WEAK", "UNKNOWN"),
    "capability_breadth": ("NARROW", "MODERATE", "BROAD", "UNKNOWN"),
    "data_sensitivity": ("PUBLIC", "INTERNAL", "SENSITIVE", "CRITICAL", "UNKNOWN"),
    "network_egress": ("NONE", "INTERNAL", "EXTERNAL", "ARBITRARY", "UNKNOWN"),
    "maintainer_trust": ("ESTABLISHED", "VERIFIED", "COMMUNITY", "UNKNOWN_AUTHOR",
                         "SUSPICIOUS"),
    "exploit_surface": ("MINIMAL", "LIMITED", "MODERATE", "BROAD", "UNKNOWN"),
}


@contextmanager
def _session_scope() -> Iterator[Session]:
    provider = get_session()
    session = None
    try:
        if hasattr(provider, "__next__"):
            session = next(provider)
        elif hasattr(provider, "__enter__"):
            session = provider.__enter__()
        else:
            session = provider
        yield session
    finally:
        if session is not None and hasattr(session, "close"):
            session.close()
        if provider is not session and hasattr(provider, "close"):
            provider.close()


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def generate_axis_label_taxonomy_report(db_session: Session) -> Dict[str, Any]:
    """Taxonomy drift report for the axis labels stored in mcp_llm_axis_scores."""
    rows = db_session.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.scored_at,
        ).where(McpLlmAxisScore.axis_name.in_(VALID_AXES))
    ).all()

    # axis -> (label, label_index) -> {count, last_seen_at}
    buckets: Dict[str, Dict[Tuple[str, Optional[int]], Dict[str, Any]]] = {}
    for axis_name, label, label_index, scored_at in rows:
        if not axis_name or axis_name not in VALID_AXES or label is None:
            continue
        idx = None if label_index is None else int(label_index)
        key = (str(label), idx)
        slot = buckets.setdefault(str(axis_name), {}).setdefault(
            key, {"count": 0, "last_seen_at": None})
        slot["count"] += 1
        stamp = _iso(scored_at)
        if stamp and (slot["last_seen_at"] is None or stamp > slot["last_seen_at"]):
            slot["last_seen_at"] = stamp

    axes: List[Dict[str, Any]] = []
    for axis_name in VALID_AXES:
        bucket = buckets.get(axis_name)
        if not bucket:
            continue

        labels = [
            {
                "label": label,
                "label_index": idx,
                "count": data["count"],
                "last_seen_at": data["last_seen_at"],
            }
            for (label, idx), data in sorted(
                bucket.items(), key=lambda kv: (kv[0][1] is None, kv[0][1], kv[0][0]))
        ]

        observed_indexes = {idx for (_lbl, idx) in bucket if idx is not None}
        ladder = AXIS_LABEL_LADDERS.get(axis_name, ())
        expected_indexes = set(range(len(ladder)))
        missing = sorted(expected_indexes - observed_indexes)

        by_index: Dict[int, List[str]] = {}
        by_label: Dict[str, List[int]] = {}
        for label, idx in bucket:
            if idx is not None:
                by_index.setdefault(idx, []).append(label)
            by_label.setdefault(label, [])
            if idx is not None and idx not in by_label[label]:
                by_label[label].append(idx)

        conflicts: List[Dict[str, Any]] = []
        for idx, lbls in sorted(by_index.items()):
            distinct = sorted(set(lbls))
            if len(distinct) > 1:
                conflicts.append({"label_index": idx, "labels": distinct,
                                  "reason": "index_maps_to_multiple_labels"})
        for label, idxs in sorted(by_label.items()):
            if len(idxs) > 1:
                conflicts.append({"label": label, "label_indexes": sorted(idxs),
                                  "reason": "label_maps_to_multiple_indexes"})

        axes.append({
            "axis_name": axis_name,
            "labels": labels,
            "label_count": len(labels),
            "missing_label_indexes": missing,
            "index_conflicts": conflicts,
        })

    return {
        "axes": axes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_report(session: Optional[Session] = None) -> Dict[str, Any]:
    """Report using the injected session, or a fresh one from app.db.get_session."""
    if session is not None:
        return generate_axis_label_taxonomy_report(session)
    with _session_scope() as s:
        return generate_axis_label_taxonomy_report(s)


try:  # FastAPI surface is optional -- the module works standalone.
    from fastapi import APIRouter, Depends

    router = APIRouter(prefix="/api", tags=["axis-taxonomy"])

    @router.get("/axis-label-taxonomy")
    def axis_label_taxonomy(db: Session = Depends(get_session)) -> dict:
        """Taxonomy drift report for the stored axis labels."""
        return generate_axis_label_taxonomy_report(db)
except Exception:  # pragma: no cover - fastapi always present in deploy
    router = None


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="CRITICAL", label_index=0,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=2, server_id="srv2", axis_name="overall_risk",
                          label="HIGH", label_index=1,
                          model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=3, server_id="srv1", axis_name="auth_strength",
                          label="SAFE", label_index=0,
                          model_version="v3.0_40974559"))
    s.commit()

    report = generate_axis_label_taxonomy_report(s)
    by_axis = {a["axis_name"]: a for a in report["axes"]}
    assert "overall_risk" in by_axis, report
    assert "auth_strength" in by_axis, report
    assert by_axis["overall_risk"]["label_count"] == 2, report
    assert {l["label"] for l in by_axis["overall_risk"]["labels"]} == {"CRITICAL", "HIGH"}, report
    assert by_axis["auth_strength"]["missing_label_indexes"], report
    assert all(not a["index_conflicts"] for a in report["axes"]), report
    assert report["generated_at"], report

    # edge case: an index mapping to two different labels is an index conflict
    s.add(McpLlmAxisScore(id=4, server_id="srv3", axis_name="overall_risk",
                          label="LOW", label_index=0,
                          model_version="v3.0_40974559"))
    s.commit()
    report2 = generate_axis_label_taxonomy_report(s)
    by_axis2 = {a["axis_name"]: a for a in report2["axes"]}
    assert by_axis2["overall_risk"]["index_conflicts"], report2

    app = FastAPI()
    if router is not None:
        app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    app.dependency_overrides[get_session] = _override_session
    c = TestClient(app)
    r = c.get("/api/axis-label-taxonomy")
    assert r.status_code == 200, r.text
    j = r.json()
    assert {a["axis_name"] for a in j["axes"]} >= {"overall_risk", "auth_strength"}, j
    s.close()

    print("PASS")
