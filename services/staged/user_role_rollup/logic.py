"""logic.py -- data/computation for the user_role_rollup service.

Provides a pure function that aggregates user counts by role and by org.
Mirrors the exemplar pattern: imports the real data layer (app.db / app.models)
and works over a SQLAlchemy Session.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import Org, User


def user_role_rollup(db: Session) -> Dict:
    """Aggregate user information.

    Returns a mapping:
        {
            "total_users": int,
            "roles": [{"role": str, "count": int}, ...],
            "orgs": [
                {
                    "org_id": int,
                    "count": int,
                    "distinct_roles": [str, ...],
                },
                ...
            ],
        }

    Role strings are normalised to lower‑case. Empty or NULL roles are bucketed
    under the literal string ``"unknown"``.
    """
    # Initialise aggregators
    total_users = 0
    role_counts: Dict[str, int] = defaultdict(int)
    org_agg: Dict[int, Dict] = defaultdict(lambda: {"count": 0, "roles": set()})

    # Pull the minimal columns needed; processing is done in Python to stay
    # portable across SQLite (used in the self‑test) and PostgreSQL.
    rows = db.execute(
        select(User.id, User.org_id, User.role)
    ).all()

    for _uid, org_id, role in rows:
        total_users += 1

        # Normalise role
        norm_role = (role or "").strip().lower()
        if not norm_role:
            norm_role = "unknown"

        role_counts[norm_role] += 1

        org_entry = org_agg[org_id]
        org_entry["count"] += 1
        org_entry["roles"].add(norm_role)

    # Build the output structures
    roles_list: List[Dict] = [
        {"role": role, "count": count} for role, count in sorted(role_counts.items())
    ]

    orgs_list: List[Dict] = []
    for org_id, data in sorted(org_agg.items()):
        orgs_list.append(
            {
                "org_id": org_id,
                "count": data["count"],
                "distinct_roles": sorted(data["roles"]),
            }
        )

    return {
        "total_users": total_users,
        "roles": roles_list,
        "orgs": orgs_list,
    }


# --------------------------------------------------------------------------- #
# Self‑test executed when the module is run directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # In‑memory SQLite engine – mimics the real DB for the test.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # Seed data: 2 orgs, 5 users, 3 distinct roles (one empty/unknown).
    org1 = Org(id=1, name="Org One", created_at=datetime.utcnow())
    org2 = Org(id=2, name="Org Two", created_at=datetime.utcnow())
    db.add_all([org1, org2])
    db.flush()  # ensure org IDs are persisted for FK constraints

    users = [
        User(
            id=1,
            email="alice@example.com",
            org_id=1,
            password_hash="hash",
            role="Admin",
            created_at=datetime.utcnow(),
        ),
        User(
            id=2,
            email="bob@example.com",
            org_id=1,
            password_hash="hash",
            role="User",
            created_at=datetime.utcnow(),
        ),
        User(
            id=3,
            email="carol@example.com",
            org_id=2,
            password_hash="hash",
            role="admin",  # same as "admin" after lower‑casing
            created_at=datetime.utcnow(),
        ),
        User(
            id=4,
            email="dave@example.com",
            org_id=2,
            password_hash="hash",
            role="",  # should map to "unknown"
            created_at=datetime.utcnow(),
        ),
        User(
            id=5,
            email="eve@example.com",
            org_id=1,
            password_hash="hash",
            role="User",
            created_at=datetime.utcnow(),
        ),
    ]
    db.add_all(users)
    db.commit()

    # Run the aggregation
    payload = user_role_rollup(db)

    # Assertions per the acceptance criteria
    assert payload["total_users"] == 5, "total_users mismatch"
    assert sum(r["count"] for r in payload["roles"]) == 5, "role counts sum mismatch"

    unknown_roles = [r for r in payload["roles"] if r["role"] == "unknown"]
    assert len(unknown_roles) == 1, "missing unknown role bucket"
    assert unknown_roles[0]["count"] == 1, "unknown role count mismatch"

    print("PASS")