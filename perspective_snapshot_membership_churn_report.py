"""perspective_snapshot_membership_churn_report.py -- membership churn between
consecutive perspective_snapshots.

Quantifies how much a saved perspective's membership set moves between two
consecutive snapshots: servers added, servers removed, and a churn ratio
(Jaccard distance over the union of the two member sets).

Interfaces:
    compute_churn(snapshots: list[dict]) -> dict   (pure, stdlib only)
    build_report(conn) -> dict                       (caller-provided cursor/connection)
    main() -> int                                   (entry point over the app data layer)

Data access is READ-ONLY. The real connector reads the APP plane
(perspective_snapshots) through the app SQLAlchemy session
(app.db.get_session + app.models.PerspectiveSnapshot); all SQL runs against the
caller-provided cursor with bound parameters -- no string interpolation of
values, no writes, no network.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.db import get_session
from app.models import PerspectiveSnapshot

SNAPSHOT_SQL = (
    "SELECT perspective_id, taken_at, membership FROM perspective_snapshots"
)


# --------------------------------------------------------------- membership

def _server_ids(membership: Any) -> Set[str]:
    """Normalise a membership blob into a set of server ids."""
    out: Set[str] = set()
    if membership is None:
        return out
    if isinstance(membership, str):
        text = membership.strip()
        if not text:
            return out
        try:
            membership = json.loads(text)
        except Exception:
            return out
    if isinstance(membership, dict):
        for key, val in membership.items():
            if isinstance(key, str) and key:
                out.add(key)
            if isinstance(val, dict):
                sid = val.get("server_id") or val.get("id")
                if isinstance(sid, str) and sid:
                    out.add(sid)
        return out
    if isinstance(membership, (list, tuple, set)):
        for item in membership:
            if isinstance(item, str):
                if item:
                    out.add(item)
            elif isinstance(item, dict):
                sid = item.get("server_id") or item.get("id")
                if isinstance(sid, str) and sid:
                    out.add(sid)
    return out


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    """Field access that tolerates ORM objects, dicts and sequences."""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    val = getattr(row, key, default)
    if val is None and isinstance(row, (list, tuple)) and row:
        val = row[0]
    return val


def _iso(value: Any) -> Optional[str]:
    """Normalise a timestamp to an ISO-8601 string (None stays None)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return str(value)


def _sort_key(value: Any) -> Tuple[int, str, str]:
    """Order snapshots chronologically; unparseable timestamps sort last."""
    raw = _iso(value) or ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (0, parsed.astimezone(timezone.utc).isoformat(), raw)
    except Exception:
        return (1, raw, raw)


# --------------------------------------------------------------- churn core

def compute_churn(snapshots: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure churn computation over snapshot rows.

    Each row: {"perspective_id": str, "taken_at": ISO-8601 str,
               "membership": list[str] of server_ids}.

    Returns {"perspectives": [{perspective_id, snapshots, added, removed,
    churn_ratio, last_taken_at}], "totals": {perspectives, added, removed,
    mean_churn_ratio}}.
    """
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in snapshots or []:
        pid = _row_get(row, "perspective_id")
        if not isinstance(pid, str) or not pid:
            continue
        grouped.setdefault(pid, []).append(row)

    perspectives: List[Dict[str, Any]] = []
    for pid in sorted(grouped):
        rows = sorted(grouped[pid], key=lambda r: _sort_key(_row_get(r, "taken_at")))
        added = 0
        removed = 0
        ratios: List[float] = []
        for prev, cur in zip(rows, rows[1:]):
            prev_ids = _server_ids(_row_get(prev, "membership"))
            cur_ids = _server_ids(_row_get(cur, "membership"))
            pair_added = len(cur_ids - prev_ids)
            pair_removed = len(prev_ids - cur_ids)
            added += pair_added
            removed += pair_removed
            union = len(prev_ids) + len(cur_ids)
            ratios.append((pair_added + pair_removed) / union if union else 0.0)
        churn_ratio = round(sum(ratios) / len(ratios), 6) if ratios else 0.0
        perspectives.append({
            "perspective_id": pid,
            "snapshots": len(rows),
            "added": added,
            "removed": removed,
            "churn_ratio": churn_ratio,
            "last_taken_at": _iso(_row_get(rows[-1], "taken_at")) if rows else None,
        })

    total_added = sum(p["added"] for p in perspectives)
    total_removed = sum(p["removed"] for p in perspectives)
    mean_ratio = (round(sum(p["churn_ratio"] for p in perspectives) / len(perspectives), 6)
                  if perspectives else 0.0)
    return {
        "perspectives": perspectives,
        "totals": {
            "perspectives": len(perspectives),
            "added": total_added,
            "removed": total_removed,
            "mean_churn_ratio": mean_ratio,
        },
    }


# --------------------------------------------------------------- connectors

class SessionCursor:
    """Adapter: an app.db SQLAlchemy Session presented as a DB-API cursor.

    The SQL is executed with bound parameters by the session -- user values are
    never interpolated into the statement text.
    """

    def __init__(self, session: Any):
        self._session = session

    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        from sqlalchemy import text
        return self._session.execute(text(sql), params or {})


def _fetch_snapshots(conn: Any, perspective_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read snapshot rows through the caller-provided cursor."""
    sql = SNAPSHOT_SQL
    params: Dict[str, Any] = {}
    if perspective_id:
        sql = sql + " WHERE perspective_id = :perspective_id"
        params["perspective_id"] = perspective_id
    rows = conn.execute(sql, params)
    out: List[Dict[str, Any]] = []
    for row in rows:
        out.append({
            "perspective_id": _row_get(row, "perspective_id"),
            "taken_at": _row_get(row, "taken_at"),
            "membership": _row_get(row, "membership"),
        })
    return out


def build_report(conn: Any, perspective_id: Optional[str] = None) -> Dict[str, Any]:
    """Build the churn report from a caller-provided cursor/connection."""
    if conn is None:
        raise ValueError("a cursor/connection is required")
    return compute_churn(_fetch_snapshots(conn, perspective_id))


# --------------------------------------------------------------- entry point

def summarize(result: Dict[str, Any]) -> str:
    """Human-readable rendering of a churn report."""
    result = result or {}
    totals = result.get("totals") or {}
    lines = [
        "Perspective membership churn report",
        f"  perspectives:     {totals.get('perspectives', 0)}",
        f"  servers added:    {totals.get('added', 0)}",
        f"  servers removed:  {totals.get('removed', 0)}",
        f"  mean churn ratio: {totals.get('mean_churn_ratio', 0.0)}",
    ]
    for p in result.get("perspectives") or []:
        lines.append(
            f"    - {p.get('perspective_id')} snapshots={p.get('snapshots')} "
            f"added={p.get('added')} removed={p.get('removed')} "
            f"churn_ratio={p.get('churn_ratio')} last={p.get('last_taken_at')}")
    return "\n".join(lines)


def main() -> int:
    """Run the churn report against the live app data layer (read-only)."""
    try:
        db = next(get_session())
    except Exception as exc:
        print(f"churn report unavailable: {exc}")
        return 1
    try:
        result = build_report(SessionCursor(db))
    except Exception as exc:
        print(f"churn report failed: {exc}")
        return 1
    finally:
        try:
            db.close()
        except Exception:
            pass
    print(summarize(result))
    return 0


# --------------------------------------------------------------- self-test

class _FakeCursor:
    """Cursor-shaped stub: records the SQL/params it was handed."""

    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.calls: List[Tuple[str, Dict[str, Any]]] = []

    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None):
        self.calls.append((sql, dict(params or {})))
        if "WHERE perspective_id = :perspective_id" in sql:
            pid = (params or {}).get("perspective_id")
            rows = [r for r in self._rows if r.get("perspective_id") == pid]
        else:
            rows = self._rows
        return list(rows)


def _self_test() -> None:
    # 1) identical pair -> churn_ratio 0.0
    identical = [
        {"perspective_id": "p_same", "taken_at": "2026-01-01T00:00:00",
         "membership": ["s1", "s2"]},
        {"perspective_id": "p_same", "taken_at": "2026-01-02T00:00:00",
         "membership": ["s1", "s2"]},
    ]
    r = compute_churn(identical)
    p = r["perspectives"][0]
    assert p["perspective_id"] == "p_same", r
    assert p["snapshots"] == 2, r
    assert p["added"] == 0 and p["removed"] == 0, r
    assert p["churn_ratio"] == 0.0, r
    assert p["last_taken_at"] == "2026-01-02T00:00:00", r
    assert r["totals"] == {"perspectives": 1, "added": 0, "removed": 0,
                            "mean_churn_ratio": 0.0}, r

    # 2) disjoint pair -> churn_ratio 1.0, added == removed == 2
    disjoint = [
        {"perspective_id": "p_flip", "taken_at": "2026-02-01T00:00:00",
         "membership": ["s1", "s2"]},
        {"perspective_id": "p_flip", "taken_at": "2026-02-02T00:00:00",
         "membership": ["s3", "s4"]},
    ]
    r = compute_churn(disjoint)
    p = r["perspectives"][0]
    assert p["added"] == 2 and p["removed"] == 2, r
    assert p["churn_ratio"] == 1.0, r
    assert r["totals"]["mean_churn_ratio"] == 1.0, r

    # 3) overlapping pair -> exact added/removed counts
    overlapping = [
        {"perspective_id": "p_mix", "taken_at": "2026-03-01T00:00:00",
         "membership": ["s1", "s2", "s3"]},
        {"perspective_id": "p_mix", "taken_at": "2026-03-02T00:00:00",
         "membership": ["s2", "s3", "s4"]},
    ]
    r = compute_churn(overlapping)
    p = r["perspectives"][0]
    assert p["added"] == 1 and p["removed"] == 1, r
    assert abs(p["churn_ratio"] - (2 / 6)) < 1e-6, r

    # build_report over a cursor: parameter binding, no interpolation
    cursor = _FakeCursor(identical + disjoint)
    report = build_report(cursor)
    assert report["totals"]["perspectives"] == 2, report
    assert report["totals"]["added"] == 2, report
    assert report["totals"]["removed"] == 2, report
    assert len(cursor.calls) == 1, cursor.calls
    sql, params = cursor.calls[0]
    assert "perspective_snapshots" in sql, sql
    assert params == {}, params

    scoped = build_report(cursor, perspective_id="p_same")
    assert scoped["totals"]["perspectives"] == 1, scoped
    sql, params = cursor.calls[-1]
    assert ":perspective_id" in sql, sql
    assert params == {"perspective_id": "p_same"}, params

    # edge cases: single snapshot, empty input, dict-shaped membership
    assert compute_churn([])["totals"]["perspectives"] == 0
    single = compute_churn([{"perspective_id": "p_one", "taken_at": "2026-04-01T00:00:00",
                             "membership": ["s1"]}])
    assert single["perspectives"][0]["churn_ratio"] == 0.0, single
    assert single["perspectives"][0]["snapshots"] == 1, single
    dicty = compute_churn([
        {"perspective_id": "p_dict", "taken_at": "2026-05-01T00:00:00",
         "membership": {"s1": "HIGH"}},
        {"perspective_id": "p_dict", "taken_at": "2026-05-02T00:00:00",
         "membership": {"s1": "HIGH", "s2": "LOW"}},
    ])
    assert dicty["perspectives"][0]["added"] == 1, dicty

    # real app models: seed snapshots in a local SQLite session and read them back
    from datetime import datetime as _dt
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(PerspectiveSnapshot(id=1, perspective_id="p_live",
                              taken_at=_dt(2026, 6, 1, 0, 0, 0),
                              membership=["s1", "s2"]))
    s.add(PerspectiveSnapshot(id=2, perspective_id="p_live",
                              taken_at=_dt(2026, 6, 2, 0, 0, 0),
                              membership=["s2", "s3"]))
    s.commit()

    live = build_report(SessionCursor(s))
    lp = live["perspectives"][0]
    assert lp["perspective_id"] == "p_live", live
    assert lp["added"] == 1 and lp["removed"] == 1, live
    assert lp["snapshots"] == 2, live
    s.close()

    assert "churn" in summarize(live).lower()
    print("PASS")


if __name__ == "__main__":
    _self_test()
