"""facet_enum_service.py -- the deterministic facet universe for Perspectives.

v1.1 "Perspectives" foundation (PRODUCT_SPEC Appendix F; council 2026-06-27,
FATHER ruling). Facets are derived ONLY from real columns:
  - mcp_server_registry: risk_tier, verdict, registry_source, trust_score
    (equal-width quartile bands over the live min/max -> "trust_band")
  - mcp_llm_axis_scores: (axis_name, label) pairs for the 7 axes, filtered to
    the LATEST global model_version -> facet keys "axis:<axis_name>"
No invented facets (hosting-model / data-residency are OUT until real columns
exist). Zero per-query LLM. Imports the REAL app data layer (app.db /
app.models) -- no stubs, no write_service round-trips.

Pure business fn: compute_facets(db) -> {facet_key: [{"value","count"}]}.
Router: GET /api/facets (authenticated), stale-while-revalidate TTL cache.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
import time
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from clerk_m2m_auth import Principal, get_principal

router = APIRouter(prefix="/api", tags=["perspectives"])

REGISTRY_FACETS = ("risk_tier", "verdict", "registry_source")
AXES = ("overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
        "network_egress", "maintainer_trust", "exploit_surface")
TRUST_BANDS = ("0-25%", "25-50%", "50-75%", "75-100%")

_CACHE: dict = {"at": 0.0, "facets": None}
_REFRESH_LOCK = threading.Lock()
# 5 min fresh TTL + 1h stale-while-revalidate window kept for config compatibility;
# warm/persisted caches ALWAYS serve stale-while-revalidate immediately so user requests
# never block on full-table GROUP BYs over 600k+ registry rows.
CACHE_TTL_SECS = 300
STALE_MAX_SECS = 3600
FACETS_STATS_KEY = "facets_cache"


def latest_global_model_version(db: Session) -> Optional[str]:
    """The production-latest model_version across the whole score table --
    same ordering rule verdict_breakdown_api uses per server."""
    row = db.execute(
        select(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(1)
    ).first()
    return row[0] if row else None


def trust_band_for(score: Optional[float], lo: float, hi: float) -> Optional[str]:
    """Equal-width quartile band for a trust_score given the live [lo, hi]
    range. Deterministic and portable (no DB percentile functions)."""
    if score is None or hi <= lo:
        return None
    frac = (score - lo) / (hi - lo)
    idx = min(3, max(0, int(frac * 4)))
    return TRUST_BANDS[idx]


def compute_facets(db: Session) -> Dict[str, List[dict]]:
    """The full facet universe with counts. One bounded aggregate per facet."""
    facets: Dict[str, List[dict]] = {}

    for col_name in REGISTRY_FACETS:
        col = getattr(McpServerRegistry, col_name)
        rows = db.execute(
            select(col, func.count()).where(col.is_not(None)).group_by(col)
        ).all()
        facets[col_name] = sorted(
            ({"value": str(v), "count": int(c)} for v, c in rows if v),
            key=lambda d: -d["count"])

    lo, hi = db.execute(
        select(func.min(McpServerRegistry.trust_score),
               func.max(McpServerRegistry.trust_score))
    ).one()
    band_counts = {b: 0 for b in TRUST_BANDS}
    if lo is not None and hi is not None and hi > lo:
        # SQL-side banding (portable CASE; sqlite + Postgres). The prior
        # implementation pulled every trust_score into Python -- 65k rows per
        # cold call in prod (treewalk perf finding).
        lo_f, width = float(lo), (float(hi) - float(lo)) / 4.0
        ts = McpServerRegistry.trust_score
        band = case((ts < lo_f + width, TRUST_BANDS[0]),
                    (ts < lo_f + 2 * width, TRUST_BANDS[1]),
                    (ts < lo_f + 3 * width, TRUST_BANDS[2]),
                    else_=TRUST_BANDS[3])
        for b, c in db.execute(select(band, func.count())
                               .where(ts.is_not(None)).group_by(band)):
            band_counts[str(b)] = int(c)
    facets["trust_band"] = [{"value": b, "count": band_counts[b]} for b in TRUST_BANDS]

    mv = latest_global_model_version(db)
    if mv:
        rows = db.execute(
            select(McpLlmAxisScore.axis_name, McpLlmAxisScore.label, func.count())
            .where(McpLlmAxisScore.model_version == mv,
                   McpLlmAxisScore.label.is_not(None))
            .group_by(McpLlmAxisScore.axis_name, McpLlmAxisScore.label)
        ).all()
        for axis, label, count in rows:
            facets.setdefault(f"axis:{axis}", []).append(
                {"value": str(label), "count": int(count)})
        for k in list(facets):
            if k.startswith("axis:"):
                facets[k].sort(key=lambda d: -d["count"])

    # vuln facet extension (P2 2026-07): kill-switch aware, best-effort --
    # mirrors app.main's optional-router mounting; an off switch or a missing
    # module leaves the facet universe untouched.
    try:
        from vuln_facet_extension import extend_facets
        extend_facets(db, facets)
    except Exception:
        pass
    return facets


def _parse_updated_at(raw) -> float:
    """Convert persisted app_stats.updated_at into UTC epoch seconds (0.0 fallback)."""
    if raw is None:
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(raw, str):
        s = raw.strip()
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:
            try:
                return float(s)
            except Exception:
                return 0.0
    return 0.0


def _dialect_name(db: Session) -> str:
    bind = db.get_bind() if hasattr(db, "get_bind") else getattr(db, "bind", None)
    return (getattr(getattr(bind, "dialect", None), "name", "") or "").lower()


def _load_persisted_facets(db: Session) -> Optional[tuple[Dict[str, List[dict]], float]]:
    """Best-effort cold-start hydrate from app_stats (returns None if absent/empty)."""
    try:
        row = db.execute(
            text("SELECT value, updated_at FROM app_stats WHERE key = :k"),
            {"k": FACETS_STATS_KEY},
        ).first()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return None
    if not row or not row[0]:
        return None
    raw_val, raw_ts = row[0], row[1]
    try:
        parsed = raw_val if isinstance(raw_val, dict) else json.loads(raw_val)
    except Exception:
        return None
    if not isinstance(parsed, dict) or not parsed:
        return None
    return parsed, _parse_updated_at(raw_ts)


def _save_persisted_facets(db: Session, facets: Dict[str, List[dict]]) -> None:
    """Best-effort upsert of computed facets into app_stats across PG and SQLite."""
    try:
        payload = json.dumps(facets)
        if _dialect_name(db) == "postgresql":
            db.execute(
                text(
                    "INSERT INTO app_stats(key, value, updated_at) "
                    "VALUES (:k, CAST(:v AS jsonb), now()) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()"
                ),
                {"k": FACETS_STATS_KEY, "v": payload},
            )
        else:
            db.execute(
                text(
                    "INSERT INTO app_stats(key, value, updated_at) "
                    "VALUES (:k, :v, :ts) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
                ),
                {"k": FACETS_STATS_KEY, "v": payload, "ts": datetime.now(timezone.utc).isoformat()},
            )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _run_locked_refresh(session_factory=None) -> None:
    """Execute one refresh under already-acquired `_REFRESH_LOCK`, always releasing it."""
    db = None
    try:
        if session_factory is None:
            from app.db import SessionLocal
            db = SessionLocal()
        else:
            db = session_factory()
        facets = compute_facets(db)
        _CACHE["facets"] = facets
        _CACHE["at"] = time.time()
        _save_persisted_facets(db, facets)
    except Exception:
        pass
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
        _REFRESH_LOCK.release()


def _trigger_bg_refresh(session_factory=None) -> None:
    """Refresh `_CACHE["facets"]` asynchronously under a non-blocking lock."""
    if not _REFRESH_LOCK.acquire(blocking=False):
        return
    try:
        threading.Thread(
            target=_run_locked_refresh,
            args=(session_factory,),
            daemon=True,
        ).start()
    except Exception:
        _REFRESH_LOCK.release()


def start_periodic_refresher(
    interval_secs: int = 1800,
    session_factory=None,
) -> threading.Event:
    """Warm facets on boot (hydrating from app_stats first) and refresh every `interval_secs`."""
    stop_event = threading.Event()

    def _loop() -> None:
        if _CACHE["facets"] is None:
            db = None
            try:
                if session_factory is None:
                    from app.db import SessionLocal
                    db = SessionLocal()
                else:
                    db = session_factory()
                loaded = _load_persisted_facets(db)
                if loaded is not None and _CACHE["facets"] is None:
                    _CACHE["facets"], _CACHE["at"] = loaded
            except Exception:
                pass
            finally:
                if db is not None:
                    try:
                        db.close()
                    except Exception:
                        pass
        while not stop_event.is_set():
            age = time.time() - float(_CACHE["at"] or 0.0)
            if _CACHE["facets"] is None or age > CACHE_TTL_SECS:
                if _REFRESH_LOCK.acquire(blocking=False):
                    _run_locked_refresh(session_factory=session_factory)
            if stop_event.wait(interval_secs):
                break

    threading.Thread(target=_loop, daemon=True, name="facets-refresher").start()
    return stop_event


def cached_facets(db: Session, *, session_factory=None) -> Dict[str, List[dict]]:
    """Stale-while-revalidate facet cache shared by `/facets` and perspective
    queries: serves warm RAM snapshots or persisted `app_stats` snapshots
    immediately (refreshing in background whenever age > CACHE_TTL_SECS), and
    computes synchronously only when both RAM cache and `app_stats` are empty."""
    now = time.time()
    cached = _CACHE["facets"]
    if cached is not None:
        age = now - float(_CACHE["at"] or 0.0)
        if age <= CACHE_TTL_SECS:
            return cached
        _trigger_bg_refresh(session_factory=session_factory)
        return cached

    persisted = _load_persisted_facets(db)
    if persisted is not None:
        facets, persisted_at = persisted
        _CACHE["facets"] = facets
        _CACHE["at"] = persisted_at
        if (now - persisted_at) > CACHE_TTL_SECS:
            _trigger_bg_refresh(session_factory=session_factory)
        return facets

    facets = compute_facets(db)
    _CACHE["facets"] = facets
    _CACHE["at"] = time.time()
    _save_persisted_facets(db, facets)
    return facets


@router.get("/facets")
def get_facets(db: Session = Depends(get_session),
               principal: Principal = Depends(get_principal)) -> dict:
    return {"facets": cached_facets(db), "cached_at": _CACHE["at"]}


if __name__ == "__main__":
    # Self-test on an in-memory sqlite with heterogeneous sample rows.
    import os
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpLlmAxisScore as A, McpServerRegistry as R
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    LocalS = sessionmaker(bind=eng)
    s = LocalS()
    s.add_all([
        R(server_id="s1", risk_tier="HIGH", verdict="HIGH", registry_source="github", trust_score=10.0),
        R(server_id="s2", risk_tier="LOW", verdict="LOW", registry_source="npm", trust_score=90.0),
        R(server_id="s3", risk_tier="HIGH", verdict="HIGH", registry_source="github", trust_score=55.0),
        A(id=1, server_id="s1", axis_name="auth_strength", label="WEAK", model_version="v3"),
        A(id=2, server_id="s2", axis_name="auth_strength", label="STRONG", model_version="v3"),
    ])
    s.commit()
    f = compute_facets(s)
    assert {d["value"] for d in f["risk_tier"]} == {"HIGH", "LOW"}
    assert f["risk_tier"][0] == {"value": "HIGH", "count": 2}
    assert "axis:auth_strength" in f, "axis facets must be keyed axis:<name>"
    assert sum(d["count"] for d in f["registry_source"]) == 3
    assert sum(d["count"] for d in f["trust_band"]) == 3

    # SWR test: aged cache between CACHE_TTL_SECS and STALE_MAX_SECS AND past STALE_MAX_SECS
    # must serve cached immediately without blocking synchronously
    _CACHE["facets"] = {"sentinel": [{"value": "cached", "count": 1}]}
    _CACHE["at"] = time.time() - (CACHE_TTL_SECS + 10)
    served = cached_facets(s, session_factory=LocalS)
    assert "sentinel" in served

    _CACHE["facets"] = {"sentinel_expired": [{"value": "stale", "count": 1}]}
    _CACHE["at"] = time.time() - (STALE_MAX_SECS + 120)
    served_old = cached_facets(s, session_factory=LocalS)
    assert "sentinel_expired" in served_old
    print("PASS")
