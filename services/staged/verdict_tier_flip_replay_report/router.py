"""verdict_tier_flip_replay_report.py -- risk-tier flip replay report.

Replays stored LLM axis scores (mcp_llm_axis_scores) plus registry names
(mcp_server_registry) and reports how often each server's DERIVED risk tier
flips between consecutive scoring events.

Mirrors verdict_breakdown_api.py: real app.db / app.models data layer (no
inline stubs), a FastAPI router exposing the report, and a CI-safe
`if __name__ == '__main__'` self-test.

Read-only: no writes, no network calls, no DDL.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["verdict_flip"])

# The 6 product tiers + the INSUFFICIENT data-gap state, ascending risk order.
TIER_ORDER: Tuple[str, ...] = (
    "TRUSTED_GENERAL",
    "TRUSTED_RESEARCH",
    "ENTERPRISE_CONTROLLED",
    "CAUTION_LIMITED",
    "HIGH_RISK_ISOLATED",
    "KNOWN_THREAT",
    "INSUFFICIENT",
)
TIER_RANK: Dict[str, int] = {t: i for i, t in enumerate(TIER_ORDER)}

# Composite (p_top) thresholds, highest tier first.
RISK_TIER_RULES: List[Tuple[float, str]] = [
    (75.0, "TRUSTED_GENERAL"),
    (60.0, "TRUSTED_RESEARCH"),
    (45.0, "ENTERPRISE_CONTROLLED"),
    (30.0, "CAUTION_LIMITED"),
    (15.0, "HIGH_RISK_ISOLATED"),
]

# overall_risk label -> product tier (label wins over the p_top composite).
LABEL_TIER: Dict[str, str] = {
    "CRITICAL": "KNOWN_THREAT",
    "DANGER": "HIGH_RISK_ISOLATED",
    "HIGH": "HIGH_RISK_ISOLATED",
    "MEDIUM": "CAUTION_LIMITED",
    "LOW": "TRUSTED_GENERAL",
    "UNKNOWN": "INSUFFICIENT",
}

AXES: Tuple[str, ...] = (
    "overall_risk", "auth_strength", "capability_breadth", "data_sensitivity",
    "network_egress", "maintainer_trust", "exploit_surface",
)

SQL_AXES = (
    "SELECT server_id, axis_name, label, p_top, scored_at FROM mcp_llm_axis_scores "
    "WHERE scored_at IS NOT NULL AND scored_at >= :since "
    "ORDER BY server_id, scored_at, axis_name"
)
SQL_REGISTRY = "SELECT server_id, name FROM mcp_server_registry"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _as_dict(row: Any) -> Dict[str, Any]:
    """Normalise a result row (dict / SQLAlchemy Row / tuple) to a plain dict."""
    if isinstance(row, dict):
        return dict(row)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return dict(mapping)
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return {"value": row}


def _rows(result: Any) -> List[Dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, dict):
        return [result]
    return [_as_dict(r) for r in result]


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return _to_utc(value)
    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            return _to_utc(datetime.fromisoformat(raw))
        except ValueError:
            return None
    return None


def derive_tier(axis_rows: List[Dict[str, Any]]) -> str:
    """Map one scoring event's axis rows onto a product risk tier.

    A recognised overall_risk label wins; otherwise the tier comes from the
    mean p_top composite (scaled to 0-100). Too little data -> INSUFFICIENT.
    """
    rows = [r for r in (axis_rows or []) if r]
    if not rows:
        return "INSUFFICIENT"

    overall = next((r for r in rows if (r.get("axis_name") or "") == "overall_risk"), None)
    if overall is not None:
        label = str(overall.get("label") or "").strip().upper()
        if label in LABEL_TIER:
            return LABEL_TIER[label]

    p_values: List[float] = []
    for r in rows:
        raw = r.get("p_top")
        if raw is None:
            continue
        try:
            p_values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if len(p_values) < 2:
        return "INSUFFICIENT"

    composite = sum(p_values) / len(p_values)
    if composite <= 1.0:
        composite *= 100.0
    for threshold, tier in RISK_TIER_RULES:
        if composite > threshold:
            return tier
    return "KNOWN_THREAT"


def _flip_axis(prev_rows: List[Dict[str, Any]], curr_rows: List[Dict[str, Any]]) -> Optional[str]:
    """Axis with the largest p_top movement between two consecutive events."""

    def _by_axis(rows: List[Dict[str, Any]]) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for r in rows:
            name = r.get("axis_name") or ""
            raw = r.get("p_top")
            if not name or raw is None:
                continue
            try:
                out[name] = float(raw)
            except (TypeError, ValueError):
                continue
        return out

    prev = _by_axis(prev_rows)
    curr = _by_axis(curr_rows)
    shared = set(prev) & set(curr)
    if not shared:
        return "overall_risk" if ("overall_risk" in prev or "overall_risk" in curr) else None
    return max(shared, key=lambda a: abs(curr[a] - prev[a]))


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------
def build_report(conn_provider, since_days: int = 30) -> Dict[str, Any]:
    """Replay tier history and report per-server tier flips.

    conn_provider: callable returning a connection-ish object whose
    .execute(sql, params) yields rows as dicts.
    """
    days = max(0, int(since_days))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    conn = conn_provider()
    axis_rows = _rows(conn.execute(SQL_AXES, {"since": cutoff}))
    registry_rows = _rows(conn.execute(SQL_REGISTRY, {}))

    names: Dict[str, Optional[str]] = {}
    for r in registry_rows:
        sid = r.get("server_id")
        if sid is not None:
            names[str(sid)] = r.get("name")

    # server_id -> event key -> axis rows
    events: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    event_ts: Dict[str, Dict[str, Optional[datetime]]] = {}
    for r in axis_rows:
        sid = r.get("server_id")
        if sid is None:
            continue
        sid = str(sid)
        ts = _parse_ts(r.get("scored_at"))
        key = ts.isoformat() if ts is not None else "unknown"
        events.setdefault(sid, {}).setdefault(key, []).append(r)
        event_ts.setdefault(sid, {})[key] = ts

    flips: List[Dict[str, Any]] = []
    servers_examined = 0

    for sid, by_event in events.items():
        if not by_event:
            continue
        servers_examined += 1
        ordered = sorted(
            by_event.keys(),
            key=lambda k: (event_ts[sid].get(k) is None, event_ts[sid].get(k).timestamp()
                           if event_ts[sid].get(k) else 0.0, k),
        )
        prev_tier: Optional[str] = None
        prev_rows: List[Dict[str, Any]] = []
        count = 0
        from_tier: Optional[str] = None
        to_tier: Optional[str] = None
        axis: Optional[str] = None
        for key in ordered:
            rows = by_event[key]
            tier = derive_tier(rows)
            if prev_tier is not None and tier != prev_tier:
                count += 1
                from_tier, to_tier = prev_tier, tier
                axis = _flip_axis(prev_rows, rows)
            prev_tier, prev_rows = tier, rows
        if count:
            flips.append({
                "server_id": sid,
                "name": names.get(sid),
                "flips": count,
                "from_tier": from_tier,
                "to_tier": to_tier,
                "axis": axis,
            })

    flips.sort(key=lambda f: (-f["flips"], f["server_id"]))
    flipped = len(flips)
    rate = (flipped / servers_examined) if servers_examined else 0.0

    return {
        "since_days": days,
        "servers_examined": servers_examined,
        "flipped_servers": flipped,
        "flip_rate": rate,
        "flips": flips,
    }


# --------------------------------------------------------------------------
# real data layer: app.db session backed connection
# --------------------------------------------------------------------------
class _SessionConn:
    """Adapts a SQLAlchemy Session to the conn_provider contract above."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        params = params or {}
        if sql == SQL_AXES:
            stmt = select(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.axis_name,
                McpLlmAxisScore.label,
                McpLlmAxisScore.p_top,
                McpLlmAxisScore.scored_at,
            ).where(McpLlmAxisScore.scored_at >= params.get("since"))
            stmt = stmt.order_by(
                McpLlmAxisScore.server_id,
                McpLlmAxisScore.scored_at,
                McpLlmAxisScore.axis_name,
            )
        elif sql == SQL_REGISTRY:
            stmt = select(McpServerRegistry.server_id, McpServerRegistry.name)
        else:
            raise ValueError("unsupported sql")
        return self.session.execute(stmt).all()


def app_conn_provider():
    """Real conn_provider: one app.db session per call (read-only)."""
    return _SessionConn(SessionLocal())


# --------------------------------------------------------------------------
# API surface
# --------------------------------------------------------------------------
class ServerFlip(BaseModel):
    server_id: str
    name: Optional[str] = None
    flips: int
    from_tier: Optional[str] = None
    to_tier: Optional[str] = None
    axis: Optional[str] = None


class TierFlipReplayReport(BaseModel):
    since_days: int
    servers_examined: int
    flipped_servers: int
    flip_rate: float
    flips: List[ServerFlip]


@router.get("/verdict/tier-flip-replay", response_model=TierFlipReplayReport)
def tier_flip_replay(since_days: int = 30,
                      db: Session = Depends(get_session)) -> dict:
    """Tier-flip replay report over the stored LLM axis scores."""
    return build_report(lambda: _SessionConn(db), since_days=since_days)


# --------------------------------------------------------------------------
# self-test
# --------------------------------------------------------------------------
class _FakeConn:
    """Minimal conn_provider fake: .execute(sql, params) -> list of dicts."""

    def __init__(self, axis_rows: List[Dict[str, Any]], registry_rows: List[Dict[str, Any]]) -> None:
        self.axis_rows = axis_rows
        self.registry_rows = registry_rows

    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        if sql == SQL_AXES:
            return list(self.axis_rows)
        if sql == SQL_REGISTRY:
            return list(self.registry_rows)
        raise ValueError("unsupported sql")


def _seed_fake():
    t1 = datetime.now(timezone.utc) - timedelta(days=5)
    t2 = datetime.now(timezone.utc) - timedelta(days=1)
    axis_rows = [
        {"server_id": "srv1", "axis_name": "overall_risk", "label": "CRITICAL", "p_top": 0.9, "scored_at": t1},
        {"server_id": "srv1", "axis_name": "auth_strength", "label": "WEAK", "p_top": 0.8, "scored_at": t1},
        {"server_id": "srv1", "axis_name": "overall_risk", "label": "LOW", "p_top": 0.1, "scored_at": t2},
        {"server_id": "srv1", "axis_name": "auth_strength", "label": "STRONG", "p_top": 0.2, "scored_at": t2},
        {"server_id": "srv2", "axis_name": "overall_risk", "label": "HIGH", "p_top": 0.7, "scored_at": t1},
        {"server_id": "srv2", "axis_name": "capability_breadth", "label": "BROAD", "p_top": 0.6, "scored_at": t1},
        {"server_id": "srv2", "axis_name": "overall_risk", "label": "MEDIUM", "p_top": 0.4, "scored_at": t2},
        {"server_id": "srv3", "axis_name": "overall_risk", "label": "MEDIUM", "p_top": 0.4, "scored_at": t1},
    ]
    registry_rows = [
        {"server_id": "srv1", "name": "Flipper One"},
        {"server_id": "srv2", "name": "Flipper Two"},
        {"server_id": "srv3", "name": "Stable Three"},
    ]
    return axis_rows, registry_rows


if __name__ == "__main__":
    # --- fake conn_provider path (directive acceptance) ---
    axis_rows, registry_rows = _seed_fake()
    report = build_report(lambda: _FakeConn(axis_rows, registry_rows), since_days=30)

    assert report["since_days"] == 30, report
    assert report["servers_examined"] == 3, report
    assert report["flipped_servers"] >= 1, report
    assert isinstance(report["flip_rate"], float), report
    for entry in report["flips"]:
        assert "server_id" in entry and "flips" in entry, entry
        assert isinstance(entry["flips"], int) and entry["flips"] > 0, entry
        assert entry["server_id"] in ("srv1", "srv2"), entry
    assert report["flips"] == sorted(report["flips"], key=lambda f: (-f["flips"], f["server_id"])), report
    assert derive_tier([{"axis_name": "overall_risk", "label": "CRITICAL", "p_top": 0.9}]) == "KNOWN_THREAT"
    assert derive_tier([]) == "INSUFFICIENT"
    assert len(TIER_ORDER) == 7 and TIER_ORDER[-1] == "INSUFFICIENT"

    # --- real app.db path: router + dependency override (mirrors the exemplar) ---
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv_db", name="DB Server",
                            url="https://github.com/example/db-mcp",
                            registry_source="github"))
    for i, (ax, lbl) in enumerate((("overall_risk", "HIGH"), ("auth_strength", "WEAK"),
                                   ("capability_breadth", "BROAD"), ("data_sensitivity", "MODERATE"),
                                   ("network_egress", "EXTERNAL"), ("maintainer_trust", "ESTABLISHED"),
                                   ("exploit_surface", "MODERATE")), start=1):
        s.add(McpLlmAxisScore(id=i, server_id="srv_db", axis_name=ax, label=lbl,
                              model_version="v3.0_40974559"))
    s.commit()
    s.close()

    that_app = FastAPI()
    that_app.include_router(router)

    def _override_session():
        d = TS()
        try:
            yield d
        finally:
            d.close()

    that_app.dependency_overrides[get_session] = _override_session
    client = TestClient(that_app)
    r = client.get("/api/verdict/tier-flip-replay", params={"since_days": 30})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["servers_examined"] == 1, body
    assert body["flipped_servers"] == 0, body
    assert body["flip_rate"] == 0.0, body

    print("PASS")
