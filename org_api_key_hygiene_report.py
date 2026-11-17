"""org_api_key_hygiene_report.py -- org API-key hygiene report (pure, JSON-serialisable).

Reads the REAL app data layer (app.db session + app.models ApiKey / Org) and turns
the rows into a hygiene report dict: per-org key counts, stale-key ages, and a
truncated key-hash prefix (never the full hash).

The report builder itself is a PURE function (no DB writes, no network); the
row-loading helpers are the only part that touches the database.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org

STALE_THRESHOLD_DAYS = 90
HASH_PREFIX_LEN = 8


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def _parse_created_at(value: Any) -> Optional[datetime]:
    """created_at may be a datetime or an ISO-8601 string; None/missing -> None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_days(created_at: Any, now: datetime) -> int:
    dt = _parse_created_at(created_at)
    if dt is None:
        return 0
    delta = (now - dt).total_seconds()
    if delta <= 0:
        return 0
    return int(delta // 86400)


def _as_str(value: Any) -> str:
    return "" if value is None else str(value)


def _hash_prefix(key_hash: Any) -> str:
    return _as_str(key_hash)[:HASH_PREFIX_LEN]


def build_api_key_hygiene_report(api_key_rows: List[dict],
                                 org_rows: List[dict] = [],
                                 now: Optional[datetime] = None) -> dict:
    """Build the org API-key hygiene report from plain row dicts.

    api_key_rows: dicts with id, org_id, key_hash, label, created_at
                   (created_at may be an ISO-8601 str or a datetime).
    org_rows:     dicts with id, name, created_at.
    now:          reference time (defaults to datetime.now(timezone.utc)); when
                  supplied, ages are computed deterministically from it.
    """
    ref = _now(now)
    keys = list(api_key_rows or [])
    orgs = list(org_rows or [])

    org_names: Dict[str, Optional[str]] = {}
    for org in orgs:
        org_id = _as_str(org.get("id"))
        if not org_id:
            continue
        org_names[org_id] = org.get("name")

    per_org_total: Dict[str, int] = defaultdict(int)
    per_org_stale: Dict[str, int] = defaultdict(int)
    per_org_oldest: Dict[str, int] = defaultdict(int)
    stale_keys: List[dict] = []

    for row in keys:
        org_id = _as_str(row.get("org_id"))
        age = _age_days(row.get("created_at"), ref)
        per_org_total[org_id] += 1
        if age > per_org_oldest[org_id]:
            per_org_oldest[org_id] = age
        if age >= STALE_THRESHOLD_DAYS:
            per_org_stale[org_id] += 1
            stale_keys.append({
                "key_id": _as_str(row.get("id")),
                "org_id": org_id,
                "label": row.get("label"),
                "age_days": age,
                "hash_prefix": _hash_prefix(row.get("key_hash")),
            })

    all_org_ids = list(org_names.keys())
    for org_id in per_org_total:
        if org_id not in org_names:
            all_org_ids.append(org_id)

    by_org = []
    for org_id in all_org_ids:
        by_org.append({
            "org_id": org_id,
            "org_name": org_names.get(org_id),
            "total_keys": per_org_total.get(org_id, 0),
            "stale_count": per_org_stale.get(org_id, 0),
            "oldest_key_age_days": per_org_oldest.get(org_id, 0),
        })

    stale_keys.sort(key=lambda k: (-k["age_days"], k["key_id"]))

    return {
        "generated_at": ref.isoformat(),
        "org_count": len(by_org),
        "total_keys": len(keys),
        "stale_threshold_days": STALE_THRESHOLD_DAYS,
        "stale_count": len(stale_keys),
        "by_org": by_org,
        "stale_keys": stale_keys,
    }


# --------------------------------------------------------------------------- #
# Real data layer: row loading from the app Postgres via app.db's session.
# --------------------------------------------------------------------------- #

def fetch_api_key_rows(db: Session) -> List[dict]:
    """Load api_keys rows as plain dicts (read-only)."""
    rows = db.execute(select(ApiKey.id, ApiKey.org_id, ApiKey.key_hash,
                             ApiKey.label, ApiKey.created_at)).all()
    return [{"id": r[0], "org_id": r[1], "key_hash": r[2], "label": r[3],
             "created_at": r[4]} for r in rows]


def fetch_org_rows(db: Session) -> List[dict]:
    """Load orgs rows as plain dicts (read-only)."""
    rows = db.execute(select(Org.id, Org.name, Org.created_at)).all()
    return [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows]


def build_api_key_hygiene_report_from_db(db: Session,
                                         now: Optional[datetime] = None) -> dict:
    """Read ApiKey/Org rows from the app DB and build the hygiene report."""
    return build_api_key_hygiene_report(fetch_api_key_rows(db),
                                        fetch_org_rows(db),
                                        now=now)


if __name__ == "__main__":  # CI-safe self-test: pure report + real model round-trip
    import json
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    org_rows = [{"id": "org_a", "name": "Alpha", "created_at": (now - timedelta(days=400)).isoformat()},
                {"id": "org_b", "name": "Beta", "created_at": (now - timedelta(days=100)).isoformat()}]
    api_key_rows = [
        {"id": "k_old", "org_id": "org_a", "key_hash": "abcdef0123456789",
         "label": "old", "created_at": (now - timedelta(days=200)).isoformat()},
        {"id": "k_none", "org_id": "org_a", "key_hash": "0123456789abcdef",
         "label": None, "created_at": None},
        {"id": "k_new", "org_id": "org_b", "key_hash": "fedcba9876543210",
         "label": "recent", "created_at": (now - timedelta(days=5)).isoformat()},
    ]

    report = build_api_key_hygiene_report(api_key_rows, org_rows, now=now)
    assert report["total_keys"] == 3, report
    assert report["stale_count"] == 1, report
    assert report["org_count"] == 2, report
    assert len(report["by_org"]) == 2, report
    assert report["stale_threshold_days"] == 90, report
    assert report["by_org"][0]["org_id"] == "org_a", report
    assert report["by_org"][0]["total_keys"] == 2, report
    assert report["by_org"][0]["stale_count"] == 1, report
    assert report["by_org"][0]["oldest_key_age_days"] == 200, report
    assert report["by_org"][0]["org_name"] == "Alpha", report
    assert report["stale_keys"][0]["key_id"] == "k_old", report
    assert report["stale_keys"][0]["age_days"] == 200, report
    assert report["stale_keys"][0]["hash_prefix"] == "abcdef01", report
    dumped = json.dumps(report)
    assert "abcdef0123456789" not in dumped, dumped
    assert "0123456789abcdef" not in dumped, dumped
    json.loads(dumped)

    # edge case: empty input -> empty, still well-formed report
    empty = build_api_key_hygiene_report([], [], now=now)
    assert empty["total_keys"] == 0 and empty["stale_count"] == 0, empty
    assert empty["by_org"] == [], empty

    # real data layer round-trip: seed the app models, read them back via app.db
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(Org(id="org_a", name="Alpha"))
    s.add(Org(id="org_b", name="Beta"))
    s.add(ApiKey(id="k_old", org_id="org_a", key_hash="abcdef0123456789", label="old",
                 created_at=now - timedelta(days=200)))
    s.add(ApiKey(id="k_new", org_id="org_b", key_hash="fedcba9876543210", label="recent",
                 created_at=now - timedelta(days=5)))
    s.commit()

    db_report = build_api_key_hygiene_report(fetch_api_key_rows(s),
                                              fetch_org_rows(s),
                                              now=now)
    s.close()
    assert db_report["total_keys"] == 2, db_report
    assert db_report["stale_count"] == 1, db_report
    assert db_report["org_count"] == 2, db_report
    assert "abcdef0123456789" not in json.dumps(db_report), db_report

    print("PASS")
