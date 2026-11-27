# deps: fastapi, pydantic, sqlalchemy
"""Axis label consistency audit for mcp_llm_axis_scores.

Read-only. Every check is a pure function over a list of row dicts:
`audit_axis_scores(rows)` -> {axis_name: {rows, issues}} and
`summarize(report)` -> {total_issues, by_code, clean_axes}. The FastAPI
router only LOADS rows through the real app data layer
(`app.db.get_session` + `app.models.McpLlmAxisScore`) and hands them to those
functions. Nothing is written, no network call is made, and no scoring /
enrichment function is defined here.

Checks, per axis_name:
  - axis_name is one of the seven allowed values
  - label is non-empty
  - label_index is a non-negative int
  - label <-> label_index is a bijection inside an axis (no duplicate index)
  - the distinct labels of an axis occupy a contiguous 0..N-1 index range
  - probs is a JSON object/list of numbers
  - max(probs) == p_top within 1e-6
  - p_top / p_critical / p_danger lie in [0, 1]

GET  /api/axis_label_consistency          audit of the stored rows
POST /api/axis_label_consistency/audit    audit of caller-supplied rows
"""
from __future__ import annotations

import json
import math
from collections import Counter
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis-consistency"])

# The seven axes the rest of the app already uses as axis_name VALUES.
ALLOWED_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

# Tolerance for max(probs) == p_top.
PROB_TOLERANCE = 1e-6


def _coerce_probs(raw: Any) -> Any:
    """Normalise the stored `probs` value to a dict/list, or None if unusable.

    The JSON column may come back as a dict, a list, or (legacy rows) a JSON
    string. A string that does not parse is NOT silently an empty mapping --
    it is a broken value and the caller must be told.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        if isinstance(parsed, (dict, list)):
            return parsed
    return None


def _prob_values(probs: Any) -> List[float]:
    """Numeric values of a probs dict/list; empty when not usable."""
    if isinstance(probs, dict):
        values = list(probs.values())
    elif isinstance(probs, list):
        values = list(probs)
    else:
        return []
    out: List[float] = []
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return []
        fv = float(v)
        if math.isnan(fv) or math.isinf(fv):
            return []
        out.append(fv)
    return out


def _issue(server_id: Optional[str], code: str, detail: str) -> Dict[str, Any]:
    return {"server_id": server_id or "", "code": code, "detail": detail}


def _audit_row(row: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-row checks: label, label_index, probs, p_top, p_critical, p_danger."""
    issues: List[Dict[str, Any]] = []
    server_id = row.get("server_id")

    axis_name = row.get("axis_name")
    if axis_name not in ALLOWED_AXES:
        issues.append(_issue(server_id, "unknown_axis",
                             f"axis_name {axis_name!r} is not one of the seven axes"))

    label = row.get("label")
    if label is None or str(label).strip() == "":
        issues.append(_issue(server_id, "empty_label", "label is empty"))

    label_index = row.get("label_index")
    if label_index is None:
        issues.append(_issue(server_id, "missing_label_index", "label_index is null"))
    elif isinstance(label_index, bool) or not isinstance(label_index, int):
        issues.append(_issue(server_id, "invalid_label_index",
                             f"label_index {label_index!r} is not an integer"))
    elif label_index < 0:
        issues.append(_issue(server_id, "invalid_label_index",
                             f"label_index {label_index} is negative"))

    probs = _coerce_probs(row.get("probs"))
    if probs is None:
        issues.append(_issue(server_id, "invalid_probs",
                             "probs is missing or is not a JSON object/list"))
        values: List[float] = []
    else:
        values = _prob_values(probs)
        if not values:
            issues.append(_issue(server_id, "invalid_probs",
                                 "probs carries no numeric values"))

    p_top = row.get("p_top")
    if p_top is None:
        issues.append(_issue(server_id, "missing_p_top", "p_top is null"))
    else:
        try:
            p_top_f = float(p_top)
        except Exception:
            p_top_f = None
            issues.append(_issue(server_id, "invalid_probability",
                                 f"p_top {p_top!r} is not numeric"))
        if p_top_f is not None:
            if not (0.0 <= p_top_f <= 1.0):
                issues.append(_issue(server_id, "probability_out_of_range",
                                     f"p_top {p_top_f} outside [0, 1]"))
            if values and abs(max(values) - p_top_f) > PROB_TOLERANCE:
                issues.append(_issue(
                    server_id, "p_top_mismatch",
                    f"p_top {p_top_f} != max(probs) {max(values)}"))

    for name in ("p_critical", "p_danger"):
        val = row.get(name)
        if val is None:
            issues.append(_issue(server_id, "missing_probability",
                                 f"{name} is null"))
            continue
        try:
            fv = float(val)
        except Exception:
            issues.append(_issue(server_id, "invalid_probability",
                                 f"{name} {val!r} is not numeric"))
            continue
        if not (0.0 <= fv <= 1.0):
            issues.append(_issue(server_id, "probability_out_of_range",
                                 f"{name} {fv} outside [0, 1]"))

    return issues


def _audit_axis(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Axis-level checks: label <-> label_index bijection and index contiguity."""
    issues: List[Dict[str, Any]] = []

    label_to_index: Dict[str, set] = {}
    index_to_label: Dict[int, set] = {}
    first_server: Dict[Any, Optional[str]] = {}

    for row in rows:
        sid = row.get("server_id")
        label = row.get("label")
        li = row.get("label_index")
        first_server.setdefault(id(row), sid)
        if label is None or str(label).strip() == "":
            continue
        if isinstance(li, bool) or not isinstance(li, int):
            continue
        label_to_index.setdefault(str(label), set()).add(li)
        index_to_label.setdefault(li, set()).add(str(label))
        first_server.setdefault(li, sid)

    for label, indices in sorted(label_to_index.items()):
        if len(indices) > 1:
            issues.append(_issue(first_server.get(sorted(indices)[0]), "duplicate_label_index",
                                 f"label {label!r} carries label_index values {sorted(indices)}"))
    for li, labels in sorted(index_to_label.items()):
        if len(labels) > 1:
            issues.append(_issue(first_server.get(li), "duplicate_label_index",
                                 f"label_index {li} is claimed by labels {sorted(labels)}"))

    # Contiguity: the distinct labels of an axis must occupy 0..N-1.
    indices = sorted(index_to_label.keys())
    n_labels = len(label_to_index)
    expected = list(range(n_labels))
    if indices != expected:
        anchor = first_server.get(indices[0]) if indices else None
        issues.append(_issue(anchor, "non_contiguous_label_index",
                             f"label_index values {indices} are not a contiguous 0..{max(n_labels - 1, 0)} range"))
    return issues


def audit_axis_scores(rows: List[dict]) -> dict:
    """Audit axis-score rows for internal consistency.

    rows: list of dicts with keys server_id, axis_name, label, label_index,
    probs, p_top, p_critical, p_danger.

    Returns {axis_name: {"rows": int, "issues": [{server_id, code, detail}]}}.
    Pure: no DB, no network, no writes.
    """
    by_axis: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows or []:
        axis_name = str(row.get("axis_name") or "")
        by_axis.setdefault(axis_name, []).append(row)

    report: Dict[str, Any] = {}
    for axis_name, axis_rows in by_axis.items():
        issues: List[Dict[str, Any]] = []
        for row in axis_rows:
            issues.extend(_audit_row(row))
        issues.extend(_audit_axis(axis_rows))
        report[axis_name] = {"rows": len(axis_rows), "issues": issues}
    return report


def summarize(report: dict) -> dict:
    """Roll an audit report up into totals.

    Returns {total_issues, by_code: {code: count}, clean_axes: [axis_name]}.
    """
    by_code: Counter = Counter()
    clean_axes: List[str] = []
    total = 0
    for axis_name, payload in (report or {}).items():
        issues = (payload or {}).get("issues") or []
        for issue in issues:
            code = (issue or {}).get("code") or "unknown"
            by_code[code] += 1
            total += 1
        if not issues:
            clean_axes.append(axis_name)
    return {
        "total_issues": total,
        "by_code": dict(by_code),
        "clean_axes": sorted(clean_axes),
    }


def _row_to_dict(row: McpLlmAxisScore) -> Dict[str, Any]:
    return {
        "server_id": row.server_id,
        "axis_name": row.axis_name,
        "label": row.label,
        "label_index": row.label_index,
        "probs": row.probs,
        "p_top": row.p_top,
        "p_critical": row.p_critical,
        "p_danger": row.p_danger,
    }


def load_axis_score_rows(session: Session, model_version: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read stored axis-score rows through the real ORM (read-only)."""
    stmt = select(McpLlmAxisScore)
    if model_version:
        stmt = stmt.where(McpLlmAxisScore.model_version == model_version)
    rows = session.execute(stmt).scalars().all()
    return [_row_to_dict(r) for r in rows]


def latest_model_version(session: Session) -> Optional[str]:
    """Newest model_version present, or None (no scores is not 'unknown')."""
    return session.execute(
        select(McpLlmAxisScore.model_version)
        .distinct()
        .order_by(McpLlmAxisScore.model_version.desc())
        .limit(1)
    ).scalar()


class AuditPayload(BaseModel):
    rows: List[Dict[str, Any]] = []


@router.get("/axis_label_consistency")
def get_axis_label_consistency(
    model_version: Optional[str] = None,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Audit the stored mcp_llm_axis_scores rows."""
    rows = load_axis_score_rows(session, model_version)
    report = audit_axis_scores(rows)
    return {
        "model_version": model_version or latest_model_version(session),
        "rows_checked": len(rows),
        "report": report,
        "summary": summarize(report),
    }


@router.post("/axis_label_consistency/audit")
def post_axis_label_consistency(payload: AuditPayload) -> Dict[str, Any]:
    """Audit caller-supplied rows (no database access)."""
    report = audit_axis_scores(payload.rows)
    return {"rows_checked": len(payload.rows), "report": report,
            "summary": summarize(report)}


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base
    from app import models  # noqa: F401  -- register the real mappers

    # --- 1. a fully consistent axis: 3 rows, contiguous 0..2, probs == p_top ---
    consistent = [
        {"server_id": "s1", "axis_name": "overall_risk", "label": "LOW",
         "label_index": 0, "probs": {"LOW": 0.8, "MED": 0.15, "HIGH": 0.05},
         "p_top": 0.8, "p_critical": 0.05, "p_danger": 0.05},
        {"server_id": "s2", "axis_name": "overall_risk", "label": "MED",
         "label_index": 1, "probs": {"LOW": 0.2, "MED": 0.7, "HIGH": 0.1},
         "p_top": 0.7, "p_critical": 0.1, "p_danger": 0.05},
        {"server_id": "s3", "axis_name": "overall_risk", "label": "HIGH",
         "label_index": 2, "probs": {"LOW": 0.05, "MED": 0.05, "HIGH": 0.9},
         "p_top": 0.9, "p_critical": 0.9, "p_danger": 0.4},
    ]

    # --- 2. duplicate label_index inside one axis ---
    dup = [
        {"server_id": "d1", "axis_name": "auth_strength", "label": "WEAK",
         "label_index": 0, "probs": {"WEAK": 0.9, "STRONG": 0.1},
         "p_top": 0.9, "p_critical": 0.1, "p_danger": 0.1},
        {"server_id": "d2", "axis_name": "auth_strength", "label": "MOD",
         "label_index": 1, "probs": {"WEAK": 0.3, "MOD": 0.6},
         "p_top": 0.6, "p_critical": 0.2, "p_danger": 0.1},
        {"server_id": "d3", "axis_name": "auth_strength", "label": "STRONG",
         "label_index": 1, "probs": {"WEAK": 0.2, "STRONG": 0.8},
         "p_top": 0.8, "p_critical": 0.1, "p_danger": 0.1},
    ]

    # --- 3. p_top does not match max(probs) ---
    mismatch = [
        {"server_id": "m1", "axis_name": "capability_breadth", "label": "BROAD",
         "label_index": 0, "probs": {"BROAD": 0.2, "NARROW": 0.5},
         "p_top": 0.9, "p_critical": 0.3, "p_danger": 0.2},
    ]

    rep_ok = audit_axis_scores(consistent)
    assert rep_ok["overall_risk"]["rows"] == 3, rep_ok
    assert rep_ok["overall_risk"]["issues"] == [], \
        f"a consistent axis must have zero issues: {rep_ok['overall_risk']['issues']}"
    assert summarize(rep_ok)["total_issues"] == 0, summarize(rep_ok)
    assert summarize(rep_ok)["clean_axes"] == ["overall_risk"], summarize(rep_ok)

    rep_dup = audit_axis_scores(dup)
    codes_dup = {i["code"] for i in rep_dup["auth_strength"]["issues"]}
    assert "duplicate_label_index" in codes_dup, \
        f"duplicate axis must report duplicate_label_index: {rep_dup['auth_strength']['issues']}"

    rep_mm = audit_axis_scores(mismatch)
    codes_mm = {i["code"] for i in rep_mm["capability_breadth"]["issues"]}
    assert "p_top_mismatch" in codes_mm, \
        f"mismatch axis must report p_top_mismatch: {rep_mm['capability_breadth']['issues']}"

    # --- summarize totals the issue count over the combined report ---
    combined = audit_axis_scores(consistent + dup + mismatch)
    summ = summarize(combined)
    expected_total = sum(
        len(payload["issues"]) for payload in combined.values()
    )
    assert summ["total_issues"] == expected_total, (summ, expected_total)
    assert summ["by_code"].get("duplicate_label_index", 0) >= 1, summ
    assert summ["by_code"].get("p_top_mismatch", 0) >= 1, summ
    assert summ["clean_axes"] == ["overall_risk"], summ

    # --- the module reads the REAL ORM, not a table it invented ---
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    MV = "v3.0_40974559"
    n = 0
    for i, ax in enumerate(("overall_risk", "auth_strength")):
        n += 1
        session.add(McpLlmAxisScore(
            id=n, server_id=f"srv{i}", axis_name=ax, label="MODERATE",
            model_version=MV,
        ))
    session.commit()

    stored = load_axis_score_rows(session, MV)
    assert len(stored) == 2, stored
    assert stored[0]["server_id"] and stored[0]["axis_name"], stored
    assert latest_model_version(session) == MV, latest_model_version(session)

    print("PASS")
