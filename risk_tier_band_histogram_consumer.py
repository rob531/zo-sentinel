"""risk_tier_band_histogram_consumer.py -- scoring-consumer: turns mcp_llm_axis_scores
rows into a risk-tier band histogram for the app surface.

No HTTP route, no signal/enrichment logic, no DB writes, no network calls.
The histogram computation is stdlib-only and side-effect free; the reader uses the
real app data layer (app.db / app.models), never a substitute.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")

# Band definitions, most severe first. min/max are the band bounds
# (3 = CRITICAL .. 0 = LOW, -1 = unknown).
TIER_ORDER: Tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
TIER_BANDS: Dict[str, Tuple[int, int]] = {
    "CRITICAL": (3, 3),
    "HIGH": (2, 2),
    "MEDIUM": (1, 1),
    "LOW": (0, 0),
    "UNKNOWN": (-1, -1),
}

# overall_risk label -> band tier
LABEL_TO_TIER = {
    "CRITICAL": "CRITICAL",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "MODERATE": "MEDIUM",
    "LOW": "LOW",
    "MINIMAL": "LOW",
    "TRUSTED": "LOW",
}
# fallback when the label is missing/unknown: label_index -> band tier
INDEX_TO_TIER = {3: "CRITICAL", 2: "HIGH", 1: "MEDIUM", 0: "LOW"}

# composite risk-tier names (risk_tier_axis_consumer) -> band tier
RISK_TIER_TO_TIER = {
    "KNOWN_THREAT": "CRITICAL",
    "HIGH_RISK_ISOLATED": "HIGH",
    "CAUTION_LIMITED": "MEDIUM",
    "ENTERPRISE_CONTROLLED": "MEDIUM",
    "TRUSTED_RESEARCH": "LOW",
    "TRUSTED_GENERAL": "LOW",
    "INSUFFICIENT": "UNKNOWN",
}

SHARE_UNITS = 10000  # 4-decimal fixed point


def _tier_from_row(row: Optional[dict]) -> Optional[str]:
    """overall_risk row -> band tier (None when the row cannot be banded)."""
    if not row:
        return None
    label = row.get("label")
    if label:
        tier = LABEL_TO_TIER.get(str(label).strip().upper())
        if tier:
            return tier
    idx = row.get("label_index")
    if idx is not None:
        try:
            return INDEX_TO_TIER.get(int(idx))
        except (TypeError, ValueError):
            return None
    return "UNKNOWN"


def _normalize_tier(tier: str) -> str:
    t = str(tier or "").strip().upper()
    if t in TIER_BANDS:
        return t
    return RISK_TIER_TO_TIER.get(t, t)


def _severity_rank(tier: str) -> int:
    try:
        return TIER_ORDER.index(tier)
    except ValueError:
        return len(TIER_ORDER)


def _band_bounds(tier: str) -> Tuple[int, int]:
    return TIER_BANDS.get(tier, (0, 0))


def _shares(counts: List[int], total: int) -> List[float]:
    """Largest-remainder allocation so the rounded shares sum to exactly 1.0."""
    if total <= 0:
        return [0.0] * len(counts)
    exact = [c * SHARE_UNITS / total for c in counts]
    floors = [int(x) for x in exact]
    leftover = SHARE_UNITS - sum(floors)
    order = sorted(range(len(counts)), key=lambda i: (-(exact[i] - floors[i]), i))
    for k in range(leftover):
        floors[order[k % len(order)]] += 1
    return [round(f / SHARE_UNITS, 4) for f in floors]


def compute_band_histogram(axis_rows: List[dict],
                           tier_of: Optional[Callable[[str], Optional[str]]] = None) -> dict:
    """axis_rows -> {"bands", "total", "unscored", "tiers"}.

    axis_rows: dicts with server_id, axis_name, p_top, label, label_index.
    tier_of: optional callable server_id -> risk-tier string; when it yields
    nothing the band lookup over the overall_risk axis label is used.
    """
    servers: List[str] = []
    seen = set()
    overall: Dict[str, dict] = {}
    for r in axis_rows or []:
        sid = str(r.get("server_id") or "")
        if not sid:
            continue
        if sid not in seen:
            seen.add(sid)
            servers.append(sid)
        if r.get("axis_name") == "overall_risk":
            overall[sid] = r

    counts: Dict[str, int] = {}
    unscored = 0
    for sid in servers:
        tier: Optional[str] = None
        if tier_of is not None:
            try:
                tier = tier_of(sid)
            except Exception:
                tier = None
        if not tier:
            tier = _tier_from_row(overall.get(sid))
        if not tier:
            unscored += 1
            continue
        tier = _normalize_tier(tier)
        counts[tier] = counts.get(tier, 0) + 1

    tiers = sorted(counts.keys(),
                   key=lambda t: (_severity_rank(t), _band_bounds(t)[0], t))
    band_counts = [counts[t] for t in tiers]
    total = sum(band_counts)
    shares = _shares(band_counts, total)

    bands = []
    for tier, count, share in zip(tiers, band_counts, shares):
        lo, hi = _band_bounds(tier)
        bands.append({"tier": tier, "min": lo, "max": hi,
                      "count": count, "share": share})

    return {"bands": bands, "total": total, "unscored": unscored,
            "tiers": [b["tier"] for b in bands]}


def summarize_band_histogram(hist: dict) -> dict:
    """Histogram -> {"dominant_tier", "total", "coverage", "concentration"}."""
    hist = hist or {}
    bands = list(hist.get("bands") or [])
    total = int(hist.get("total") or 0)
    unscored = int(hist.get("unscored") or 0)

    dominant = ""
    dominant_count = 0
    for b in bands:
        c = int(b.get("count") or 0)
        if c > dominant_count:
            dominant = str(b.get("tier") or "")
            dominant_count = c

    universe = total + unscored
    coverage = round(total / universe, 4) if universe > 0 else 0.0
    concentration = round(dominant_count / total, 4) if total > 0 else 0.0
    return {"dominant_tier": dominant, "total": total,
            "coverage": coverage, "concentration": concentration}


def load_axis_rows(db: Session) -> List[dict]:
    """Read the axis-score rows this consumer needs from the real app tables.

    Only the newest model_version per server is considered, so a re-scored
    server is counted once, at its current tier.
    """
    versions = db.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc(), McpLlmAxisScore.id.desc())
    ).all()
    latest: Dict[str, str] = {}
    for sid, mv in versions:
        if sid not in latest:
            latest[sid] = mv

    rows = db.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name,
               McpLlmAxisScore.p_top, McpLlmAxisScore.label,
               McpLlmAxisScore.label_index, McpLlmAxisScore.model_version)
    ).all()
    out = []
    for r in rows:
        if latest.get(r[0]) != r[5]:
            continue
        out.append({"server_id": r[0], "axis_name": r[1], "p_top": r[2],
                    "label": r[3], "label_index": r[4]})
    return out


def run(db: Session = Depends(get_session)) -> dict:
    """Histogram + summary for the whole scored population."""
    hist = compute_band_histogram(load_axis_rows(db))
    return {"histogram": hist, "summary": summarize_band_histogram(hist)}


if __name__ == "__main__":  # self-test: pure functions + the real data layer
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    # ---- 1. all-server rows: every server has an overall_risk axis row ----
    rows_all = []
    _i = 0
    for sid, label, idx in (("srv_crit", "CRITICAL", 3),
                            ("srv_high", "HIGH", 2),
                            ("srv_high2", "HIGH", 2),
                            ("srv_low", "LOW", 0)):
        _i += 1
        rows_all.append({"server_id": sid, "axis_name": "overall_risk",
                         "p_top": 0.9 - _i * 0.01, "label": label,
                         "label_index": idx})
    for sid in ("srv_crit", "srv_high", "srv_high2", "srv_low"):
        rows_all.append({"server_id": sid, "axis_name": "auth_strength",
                         "p_top": 0.5, "label": "STRONG", "label_index": 3})

    hist_all = compute_band_histogram(rows_all)
    assert sum(b["count"] for b in hist_all["bands"]) == hist_all["total"], hist_all
    assert hist_all["total"] == 4, hist_all
    assert hist_all["unscored"] == 0, hist_all
    assert abs(sum(b["share"] for b in hist_all["bands"]) - 1.0) < 1e-9, hist_all
    assert hist_all["tiers"] == ["CRITICAL", "HIGH", "LOW"], hist_all
    assert [b["min"] for b in hist_all["bands"]] == [3, 2, 0], hist_all
    assert [b["max"] for b in hist_all["bands"]] == [3, 2, 0], hist_all
    assert [b["count"] for b in hist_all["bands"]] == [1, 2, 1], hist_all

    summ_all = summarize_band_histogram(hist_all)
    assert summ_all["dominant_tier"] == "HIGH", summ_all
    assert summ_all["total"] == 4, summ_all
    assert summ_all["coverage"] == 1.0, summ_all
    assert summ_all["concentration"] == 0.5, summ_all

    # ---- 2. a server missing the overall_risk axis -> unscored ----
    rows_missing = rows_all + [
        {"server_id": "srv_norisk", "axis_name": "auth_strength",
         "p_top": 0.4, "label": "WEAK", "label_index": 0},
    ]
    hist_missing = compute_band_histogram(rows_missing)
    assert hist_missing["unscored"] == 1, hist_missing
    assert hist_missing["total"] == 4, hist_missing
    assert sum(b["count"] for b in hist_missing["bands"]) == hist_missing["total"]
    assert abs(sum(b["share"] for b in hist_missing["bands"]) - 1.0) < 1e-9
    summ_missing = summarize_band_histogram(hist_missing)
    assert summ_missing["coverage"] == 0.8, summ_missing
    assert summ_missing["dominant_tier"] == "HIGH", summ_missing

    # tier_of overrides the label lookup
    hist_override = compute_band_histogram(rows_all, tier_of=lambda sid: "MEDIUM")
    assert hist_override["tiers"] == ["MEDIUM"], hist_override
    assert hist_override["total"] == 4, hist_override

    # composite risk-tier names map onto bands too
    hist_risk = compute_band_histogram(rows_all, tier_of=lambda sid: "KNOWN_THREAT")
    assert hist_risk["tiers"] == ["CRITICAL"], hist_risk
    assert hist_risk["total"] == 4, hist_risk

    # ---- 3. empty list ----
    hist_empty = compute_band_histogram([])
    assert hist_empty == {"bands": [], "total": 0, "unscored": 0, "tiers": []}, hist_empty
    summ_empty = summarize_band_histogram(hist_empty)
    assert summ_empty["total"] == 0, summ_empty
    assert summ_empty["coverage"] == 0.0, summ_empty
    assert summ_empty["concentration"] == 0.0, summ_empty
    assert summ_empty["dominant_tier"] == "", summ_empty

    # ---- 4. real data layer: seeded app tables through the app session ----
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    for _id, (sid, ax, lbl) in enumerate((("srv_crit", "overall_risk", "CRITICAL"),
                                          ("srv_crit", "auth_strength", "WEAK"),
                                          ("srv_high", "overall_risk", "HIGH"),
                                          ("srv_high2", "overall_risk", "HIGH"),
                                          ("srv_low", "overall_risk", "LOW")), start=1):
        s.add(McpLlmAxisScore(id=_id, server_id=sid, axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit()
    s.close()

    test_app = FastAPI()

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    test_app.dependency_overrides[get_session] = _override_session

    db = next(test_app.dependency_overrides[get_session]())
    try:
        db_rows = load_axis_rows(db)
        assert len(db_rows) == 5, db_rows
        db_hist = compute_band_histogram(db_rows)
        assert db_hist["total"] == 4, db_hist
        assert db_hist["unscored"] == 0, db_hist
        out = run(db)
        assert out["histogram"]["total"] == 4, out
        assert out["summary"]["dominant_tier"] == "HIGH", out
        assert out["summary"]["coverage"] == 1.0, out
    finally:
        db.close()

    print("PASS")
