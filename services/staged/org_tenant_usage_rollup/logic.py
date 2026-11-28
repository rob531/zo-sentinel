"""logic.py -- data/computation layer for org_tenant_usage_rollup service.

Provides a read‑only aggregation of Org, User and ApiKey tables.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, Org, User


def org_tenant_usage_rollup(
    db: Session, *, include_empty: bool = False
) -> Dict[str, object]:
    """Return a usage roll‑up per tenant (org).

    Args:
        db: SQLAlchemy Session.
        include_empty: If ``True`` include orgs that have no users and no API keys.

    Returns:
        Mapping with keys:
            - orgs: list of per‑org dicts
            - total_orgs: total number of orgs in the system
            - total_users: total number of users across all orgs
            - total_api_keys: total number of API keys across all orgs
    """
    # ------------------------------------------------------------------ totals
    total_orgs = db.execute(select(func.count()).select_from(Org)).scalar_one()
    total_users = db.execute(select(func.count()).select_from(User)).scalar_one()
    total_api_keys = db.execute(select(func.count()).select_from(ApiKey)).scalar_one()

    # --------------------------------------------------------------- per‑org data
    org_rows = db.execute(select(Org)).scalars().all()
    orgs_out: List[Dict[str, object]] = []

    for org in org_rows:
        # users by role
        role_counts = db.execute(
            select(User.role, func.count())
            .where(User.org_id == org.id)
            .group_by(User.role)
        ).all()
        users_by_role: Dict[str, int] = {
            role: int(cnt) for role, cnt in sorted(role_counts, key=lambda rc: rc[0])
        }
        user_count = sum(users_by_role.values())

        # api key count
        api_key_count = db.execute(
            select(func.count()).where(ApiKey.org_id == org.id)
        ).scalar_one()

        # oldest / newest user timestamps (may be None)
        oldest_user_at: datetime | None = db.execute(
            select(func.min(User.created_at)).where(User.org_id == org.id)
        ).scalar_one()
        newest_user_at: datetime | None = db.execute(
            select(func.max(User.created_at)).where(User.org_id == org.id)
        ).scalar_one()

        if not include_empty and user_count == 0 and api_key_count == 0:
            continue

        orgs_out.append(
            {
                "org_id": org.id,
                "name": org.name,
                "created_at": org.created_at,
                "user_count": user_count,
                "users_by_role": users_by_role,
                "api_key_count": int(api_key_count),
                "oldest_user_at": oldest_user_at,
                "newest_user_at": newest_user_at,
            }
        )

    return {
        "orgs": orgs_out,
        "total_orgs": int(total_orgs),
        "total_users": int(total_users),
        "total_api_keys": int(total_api_keys),
    }


# --------------------------------------------------------------------------- self‑test
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base  # type: ignore[attr-defined]

    # In‑memory SQLite for self‑test
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=__import__("sqlalchemy.pool").pool.StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    now = datetime.utcnow()

    with SessionLocal() as sess:
        # orgs
        org1 = Org(id=1, name="Org One", created_at=now)
        org2 = Org(id=2, name="Org Two", created_at=now)
        sess.add_all([org1, org2])

        # users (3 users, 2 roles)
        u1 = User(
            id=1,
            org_id=1,
            role="admin",
            created_at=now,
            email="admin1@example.com",
            password_hash="hash1",
        )
        u2 = User(
            id=2,
            org_id=1,
            role="member",
            created_at=now,
            email="member1@example.com",
            password_hash="hash2",
        )
        u3 = User(
            id=3,
            org_id=2,
            role="admin",
            created_at=now,
            email="admin2@example.com",
            password_hash="hash3",
        )
        sess.add_all([u1, u2, u3])

        # api keys (2 keys)
        k1 = ApiKey(
            id=1,
            org_id=1,
            label="key‑one",
            key_hash="khash1",
            created_at=now,
        )
        k2 = ApiKey(
            id=2,
            org_id=2,
            label="key‑two",
            key_hash="khash2",
            created_at=now,
        )
        sess.add_all([k1, k2])

        sess.commit()

        result = org_tenant_usage_rollup(sess)

        assert result["total_orgs"] == 2, "total_orgs mismatch"
        assert result["total_users"] == 3, "total_users mismatch"
        first_org = result["orgs"][0]
        assert first_org["users_by_role"]["admin"] == 1, "admin count mismatch"
        assert first_org["users_by_role"]["member"] == 1, "member count mismatch"

        print("PASS")