import datetime
from datetime import timedelta
from typing import Dict, List, Optional

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import ApiKey, Org


def _aggregate_key_data(
    session: Session, rotation_window_days: int
) -> Dict[int, Dict[str, Optional[int]]]:
    """Return a mapping of org_id -> aggregated key statistics."""
    now = datetime.datetime.utcnow()

    # Basic aggregation: count, min(created_at), max(created_at)
    agg_stmt = (
        select(
            ApiKey.org_id,
            func.count(ApiKey.id).label("key_count"),
            func.min(ApiKey.created_at).label("oldest"),
            func.max(ApiKey.created_at).label("newest"),
        )
        .group_by(ApiKey.org_id)
        .subquery()
    )

    # Stale key count per org
    stale_threshold = now - timedelta(days=rotation_window_days)
    stale_stmt = (
        select(
            ApiKey.org_id,
            func.count(ApiKey.id).label("stale_count"),
        )
        .where(ApiKey.created_at <= stale_threshold)
        .group_by(ApiKey.org_id)
        .subquery()
    )

    stmt = (
        select(
            agg_stmt.c.org_id,
            agg_stmt.c.key_count,
            agg_stmt.c.oldest,
            agg_stmt.c.newest,
            stale_stmt.c.stale_count,
        )
        .select_from(agg_stmt.outerjoin(stale_stmt, agg_stmt.c.org_id == stale_stmt.c.org_id))
    )

    result = {}
    for row in session.execute(stmt):
        org_id = row.org_id
        key_count = row.key_count or 0
        oldest = row.oldest
        newest = row.newest
        stale_count = row.stale_count or 0

        oldest_age = (now - oldest).days if oldest else None
        newest_age = (now - newest).days if newest else None

        result[org_id] = {
            "key_count": key_count,
            "oldest_key_age_days": oldest_age,
            "newest_key_age_days": newest_age,
            "stale_key_count": stale_count,
        }
    return result


def get_org_api_key_age(
    rotation_window_days: int = 90,
    session: Session = Depends(get_session),
) -> Dict:
    """
    Return API‑key age statistics per organisation.

    Shape:
    {
        "rotation_window_days": int,
        "orgs": [
            {
                "org_id": str,
                "org_name": str | None,
                "key_count": int,
                "oldest_key_age_days": int | None,
                "newest_key_age_days": int | None,
                "stale_key_count": int,
            },
            ...
        ],
        "total_keys": int,
        "total_stale_keys": int,
    }
    """
    agg = _aggregate_key_data(session, rotation_window_days)

    # Fetch all organisations (including those without keys)
    org_rows = session.execute(select(Org.id, Org.name)).all()
    orgs: List[Dict] = []
    total_keys = 0
    total_stale_keys = 0

    for org_id, org_name in org_rows:
        stats = agg.get(org_id, {
            "key_count": 0,
            "oldest_key_age_days": None,
            "newest_key_age_days": None,
            "stale_key_count": 0,
        })
        orgs.append(
            {
                "org_id": str(org_id),
                "org_name": org_name,
                "key_count": stats["key_count"],
                "oldest_key_age_days": stats["oldest_key_age_days"],
                "newest_key_age_days": stats["newest_key_age_days"],
                "stale_key_count": stats["stale_key_count"],
            }
        )
        total_keys += stats["key_count"]
        total_stale_keys += stats["stale_key_count"]

    return {
        "rotation_window_days": rotation_window_days,
        "orgs": orgs,
        "total_keys": total_keys,
        "total_stale_keys": total_stale_keys,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    SessionLocal = sessionmaker(bind=engine)

    # Create tables
    Base.metadata.create_all(engine)

    # Seed data
    now = datetime.datetime.utcnow()
    old_date = now - timedelta(days=100)   # stale (>90 days)
    recent_date = now - timedelta(days=10)  # fresh

    sess = SessionLocal()
    org1 = Org(id=1, name="Alpha", created_at=now)
    org2 = Org(id=2, name="Beta", created_at=now)
    sess.add_all([org1, org2])
    sess.flush()  # ensure org IDs are persisted

    key1 = ApiKey(id=1, org_id=org1.id, key_hash="hash1", label="old", created_at=old_date)
    key2 = ApiKey(id=2, org_id=org1.id, key_hash="hash2", label="new", created_at=recent_date)
    key3 = ApiKey(id=3, org_id=org2.id, key_hash="hash3", label="new2", created_at=recent_date)
    sess.add_all([key1, key2, key3])
    sess.commit()

    # Override dependency
    def get_test_session() -> Session:
        return sess

    # Direct call (bypassing FastAPI injection)
    result = get_org_api_key_age(rotation_window_days=90, session=sess)

    # Assertions per contract
    assert result["total_keys"] == 3, "total_keys mismatch"
    assert len(result["orgs"]) == 2, "org count mismatch"
    # Find org1 stats
    org1_stats = next(o for o in result["orgs"] if o["org_id"] == "1")
    assert org1_stats["stale_key_count"] == 1, "stale key count for org1 incorrect"
    # Overall stale count
    assert result["total_stale_keys"] == 1, "total stale keys mismatch"

    print("PASS")