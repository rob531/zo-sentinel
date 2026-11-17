"""perspective_membership_drift_report.py -- membership drift between the two
newest snapshots of every perspective.

Compares the latest perspective_snapshots membership against the previous one
and reports which servers drifted IN or OUT. Read-only: SELECTs over the APP
plane (perspectives, perspective_snapshots) through the app.db SQLAlchemy
session -- no writes, no network, no DB output (the report goes to stdout).

Interfaces:
    build_report(db_session=None, top_n=25) -> dict
    main() -> int
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_session  # noqa: F401 - get_session is the app data-layer seam
from app.models import Perspective, PerspectiveSnapshot

REPORT_VERSION = "perspective-membership-drift/1"


def _server_ids(membership: Any) -> Set[str]:
    """Normalise a membership blob into a set of server ids."""
    out: Set[str] = set()
    if membership is None:
        return out
    if isinstance(membership, (str, bytes, bytearray)):
        try:
            membership = json.loads(membership)
        except Exception:
            return out
    if isinstance(membership, dict):
        for key, val in membership.items():
            if isinstance(key, str) and key:
                out.add(key)
            elif isinstance(val, dict):
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


def _sort_key(row: Any) -> Any:
    """Order snapshots newest-first: by taken_at DESC, id DESC as tie-break."""
    taken = getattr(row, "taken_at", None)
    if isinstance(taken, datetime):
        if taken.tzinfo is not None:
            taken = taken.astimezone(timezone.utc).replace(tzinfo=None)
        return (taken, int(getattr(row, "id", 0) or 0))
    return (datetime.min, int(getattr(row, "id", 0) or 0))


def build_report(db_session: Optional[Session] = None, top_n: int = 25) -> Dict[str, Any]:
    """Compare the two newest snapshots per perspective.

    Returns {"report_version", "generated_at", "perspectives": [...],
    "totals": {...}}. `added`/`removed` are sorted server-id lists truncated to
    `top_n`; the counts and `unchanged_count`/`delta` reflect the FULL sets.
    """
    limit = max(0, int(top_n))
    owned = db_session is None
    db = db_session if db_session is not None else SessionLocal()
    try:
        names: Dict[str, str] = {}
        for pid, name in db.execute(select(Perspective.id, Perspective.name)).all():
            names[str(pid)] = name or ""

        rows = list(db.execute(
            select(PerspectiveSnapshot.perspective_id, PerspectiveSnapshot.taken_at,
                   PerspectiveSnapshot.membership, PerspectiveSnapshot.id)
        ).all())

        grouped: Dict[str, List[Any]] = {}
        for row in rows:
            pid = getattr(row, "perspective_id", None)
            if pid is None:
                continue
            grouped.setdefault(str(pid), []).append(row)

        perspectives: List[Dict[str, Any]] = []
        total_added = 0
        total_removed = 0
        for pid in sorted(grouped):
            snaps = sorted(grouped[pid], key=_sort_key, reverse=True)
            if len(snaps) < 2:
                continue
            latest = _server_ids(snaps[0].membership)
            previous = _server_ids(snaps[1].membership)
            added = sorted(latest - previous)
            removed = sorted(previous - latest)
            unchanged = len(latest & previous)
            total_added += len(added)
            total_removed += len(removed)
            perspectives.append({
                "perspective_id": pid,
                "perspective_name": names.get(pid, ""),
                "added": added[:limit],
                "removed": removed[:limit],
                "unchanged_count": unchanged,
                "delta": len(added) - len(removed),
            })

        return {
            "report_version": REPORT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "perspectives": perspectives,
            "totals": {
                "perspectives": len(perspectives),
                "added": total_added,
                "removed": total_removed,
            },
        }
    finally:
        if owned:
            try:
                db.close()
            except Exception:
                pass


def main() -> int:
    """Run the drift report against the live app data layer (read-only)."""
    try:
        report = build_report()
    except Exception as exc:
        print(f"perspective membership drift report failed: {exc}")
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
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
    s.add(Perspective(id="p1", name="High Risk Set", facet_filters={}, created_by="tester"))
    s.add(PerspectiveSnapshot(id=1, perspective_id="p1",
                              taken_at=_dt(2026, 6, 1, 0, 0, 0),
                              membership=["a", "b"]))
    s.add(PerspectiveSnapshot(id=2, perspective_id="p1",
                              taken_at=_dt(2026, 6, 2, 0, 0, 0),
                              membership=["b", "c"]))
    s.commit()

    rep = build_report(s)
    assert rep["report_version"] == REPORT_VERSION, rep
    assert len(rep["perspectives"]) == 1, rep
    p = rep["perspectives"][0]
    assert p["perspective_id"] == "p1", p
    assert p["perspective_name"] == "High Risk Set", p
    assert p["added"] == ["c"], p
    assert p["removed"] == ["a"], p
    assert p["delta"] == 0, p
    assert p["unchanged_count"] == 1, p
    assert rep["totals"] == {"perspectives": 1, "added": 1, "removed": 1}, rep
    assert isinstance(rep["generated_at"], str) and rep["generated_at"], rep

    # edge: top_n truncation + single-snapshot perspective is skipped
    s.add(Perspective(id="p2", name="One Shot", facet_filters={}, created_by="tester"))
    s.add(PerspectiveSnapshot(id=3, perspective_id="p2",
                              taken_at=_dt(2026, 6, 3, 0, 0, 0),
                              membership=["x"]))
    s.commit()
    rep2 = build_report(s, top_n=0)
    assert len(rep2["perspectives"]) == 1, rep2
    assert rep2["perspectives"][0]["added"] == [], rep2
    assert rep2["perspectives"][0]["removed"] == [], rep2
    assert rep2["totals"]["added"] == 1 and rep2["totals"]["removed"] == 1, rep2
    s.close()

    print("PASS")
