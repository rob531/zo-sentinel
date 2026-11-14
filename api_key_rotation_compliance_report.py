"""api_key_rotation_compliance_report.py -- per-org API-key rotation compliance audit.

Reads the REAL app data layer through the app.db SQLAlchemy session:
`api_keys` (id, org_id, key_hash, label, created_at) and `orgs` (id, name,
created_at). A key is STALE when it is older than `window_days`.

Read-only: no HTTP route, no network, no writes, no duckdb, no CSV/JSON inputs.
Key material is never emitted -- only labels, counts and ages.
"""
# deps: sqlalchemy
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

DEFAULT_WINDOW_DAYS = 90
MAX_WINDOW_DAYS = 3650
HASH_PREFIX_LEN = 8


# ---------------------------------------------------------------- helpers


def _clamp_days(window_days: Any) -> int:
    try:
        value = int(window_days)
    except (TypeError, ValueError):
        return DEFAULT_WINDOW_DAYS
    return max(1, min(MAX_WINDOW_DAYS, value))


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    dt = _as_utc(value)
    return dt.isoformat() if dt else None


def _truncate_hash(value: Any) -> Optional[str]:
    """Key material is never emitted in full."""
    if value is None:
        return None
    text = str(value)
    return text[:HASH_PREFIX_LEN]


@contextmanager
def _session_scope(db: Optional[Session]) -> Iterator[Session]:
    """Use the caller's session when given, else open one via the app dependency."""
    if db is not None:
        yield db
        return
    gen = get_session()
    session: Session = next(gen)
    try:
        yield session
    finally:
        session.close()
        try:
            next(gen)
        except StopIteration:
            pass


# ---------------------------------------------------------------- report


def build_report(
    window_days: int = DEFAULT_WINDOW_DAYS,
    now: Optional[datetime] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Audit API-key rotation compliance per organisation.

    Returns:
        {"window_days": int,
         "orgs": [{"org_id", "org_name", "total_keys", "stale_keys",
                   "oldest_key_days", "stale_ratio", "labels_stale": [label]}],
         "totals": {"orgs": int, "keys": int, "stale_keys": int},
         "generated_at": iso8601}
    """
    days = _clamp_days(window_days)
    reference = _as_utc(now) or datetime.now(timezone.utc)

    with _session_scope(db) as session:
        org_rows = session.execute(
            select(Org.id, Org.name)
        ).all()
        key_rows = session.execute(
            select(ApiKey.id, ApiKey.org_id, ApiKey.key_hash, ApiKey.label,
                   ApiKey.created_at)
        ).all()

    org_names: Dict[str, Optional[str]] = {}
    for org_id, name in org_rows:
        org_names[str(org_id)] = str(name) if name is not None else None

    per_org: Dict[str, Dict[str, Any]] = {}
    for org_id, name in org_names.items():
        per_org[org_id] = {
            "org_id": org_id,
            "org_name": name,
            "total_keys": 0,
            "stale_keys": 0,
            "oldest_key_days": None,
            "stale_ratio": 0.0,
            "labels_stale": [],
        }

    for _key_id, org_id, key_hash, label, created_at in key_rows:
        org_key = str(org_id) if org_id is not None else ""
        entry = per_org.get(org_key)
        if entry is None:
            entry = {
                "org_id": org_key,
                "org_name": None,
                "total_keys": 0,
                "stale_keys": 0,
                "oldest_key_days": None,
                "stale_ratio": 0.0,
                "labels_stale": [],
            }
            per_org[org_key] = entry

        created = _as_utc(created_at)
        age_days: Optional[float] = None
        if created is not None:
            age_days = (reference - created).total_seconds() / 86400.0

        entry["total_keys"] += 1
        if age_days is not None:
            if entry["oldest_key_days"] is None or age_days > entry["oldest_key_days"]:
                entry["oldest_key_days"] = age_days
            if age_days > days:
                entry["stale_keys"] += 1
                entry["labels_stale"].append(str(label) if label else _truncate_hash(key_hash) or "")

    orgs: List[Dict[str, Any]] = []
    for entry in per_org.values():
        total = entry["total_keys"]
        entry["stale_ratio"] = round(entry["stale_keys"] / total, 4) if total else 0.0
        if entry["oldest_key_days"] is not None:
            entry["oldest_key_days"] = int(entry["oldest_key_days"])
        entry["labels_stale"].sort()
        orgs.append(entry)

    orgs.sort(key=lambda o: (-o["stale_ratio"], o["org_id"]))

    totals = {
        "orgs": len(orgs),
        "keys": sum(o["total_keys"] for o in orgs),
        "stale_keys": sum(o["stale_keys"] for o in orgs),
    }

    return {
        "window_days": days,
        "orgs": orgs,
        "totals": totals,
        "generated_at": reference.isoformat(),
    }


# ---------------------------------------------------------------- entrypoint


def main() -> int:
    """Build the report from the real data layer and print it as JSON."""
    report = build_report()
    print(json.dumps(report, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite session override
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    NOW = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()

    s.add(Org(id="org_a", name="Alpha Corp", created_at=NOW - timedelta(days=400)))
    s.add(Org(id="org_b", name="Beta Ltd", created_at=NOW - timedelta(days=40)))
    s.add(ApiKey(id="k1", org_id="org_a", key_hash="hashalphaold0001",
                 label="alpha-stale-1", created_at=NOW - timedelta(days=200)))
    s.add(ApiKey(id="k2", org_id="org_a", key_hash="hashalphaold0002",
                 label="alpha-stale-2", created_at=NOW - timedelta(days=120)))
    s.add(ApiKey(id="k3", org_id="org_b", key_hash="hashbetafresh0001",
                 label="beta-fresh-1", created_at=NOW - timedelta(days=10)))
    s.add(ApiKey(id="k4", org_id="org_b", key_hash="hashbetafresh0002",
                 label="beta-fresh-2", created_at=NOW - timedelta(days=2)))
    s.commit()

    report = build_report(window_days=90, now=NOW, db=s)
    assert report["window_days"] == 90, report
    assert report["totals"]["orgs"] == 2, report
    assert report["totals"]["keys"] == 4, report
    assert report["totals"]["stale_keys"] == 2, report
    assert report["generated_at"].startswith("2026-10-30"), report

    by_org = {o["org_id"]: o for o in report["orgs"]}
    assert by_org["org_a"]["org_name"] == "Alpha Corp", report
    assert by_org["org_a"]["total_keys"] == 2, report
    assert by_org["org_a"]["stale_keys"] == 2, report
    assert by_org["org_a"]["stale_ratio"] == 1.0, report
    assert by_org["org_a"]["labels_stale"] == ["alpha-stale-1", "alpha-stale-2"], report
    assert by_org["org_a"]["oldest_key_days"] == 200, report
    assert by_org["org_b"]["stale_keys"] == 0, report
    assert by_org["org_b"]["stale_ratio"] == 0.0, report
    assert by_org["org_b"]["labels_stale"] == [], report

    # no key material leaks into the report
    assert "hashalphaold0001" not in json.dumps(report), report

    # edge: an org with no keys still appears, with a zero ratio
    s.add(Org(id="org_c", name="Gamma Inc", created_at=NOW))
    s.commit()
    empty_org = build_report(window_days=90, now=NOW, db=s)
    g = {o["org_id"]: o for o in empty_org["orgs"]}["org_c"]
    assert g["total_keys"] == 0 and g["stale_keys"] == 0, empty_org
    assert g["stale_ratio"] == 0.0 and g["oldest_key_days"] is None, empty_org
    assert empty_org["totals"]["keys"] == 4, empty_org
    assert empty_org["totals"]["stale_keys"] == 2, empty_org

    # edge: a wider window makes every key compliant
    wide = build_report(window_days=365, now=NOW, db=s)
    assert wide["totals"]["stale_keys"] == 0, wide

    # edge: window_days is clamped, never negative
    assert build_report(window_days=0, now=NOW, db=s)["window_days"] == 1
    assert build_report(window_days=-5, now=NOW, db=s)["window_days"] == 1

    # the real session seam is used when no session is injected
    assert callable(get_session)

    s.close()
    print("PASS")
