"""logic.py -- data/computation layer for unauth_access_index service.

Provides a read‑only query over McpUnauthAccess with optional filters and
joins to McpServerRegistry for display fields.  No writes, no network calls.
"""

from __future__ import annotations

from typing import List, Tuple, Dict, Any

from sqlalchemy import select, desc, asc, func
from sqlalchemy.orm import Session

from app.db import get_session, Base
from app.models import McpUnauthAccess, McpServerRegistry


def unauth_access_index(
    db: Session,
    *,
    server_id: str | None = None,
    unauth_access: str | None = None,
    limit: int = 100,
) -> Tuple[int, List[Dict[str, Any]]]:
    """Return unauth‑access rows with optional filters.

    Args:
        db: SQLAlchemy Session (provided by FastAPI Depends).
        server_id: optional exact match on McpUnauthAccess.server_id.
        unauth_access: optional exact match on McpUnauthAccess.unauth_access.
        limit: max rows to return, clamped to 1‑500 (default 100).

    Returns:
        A tuple of (count, list_of_entry_dicts).  ``count`` is the number of
        rows returned (post‑filtering, pre‑limit).  Each dict matches the
        fields of ``UnauthAccessEntry`` in the router contract.
    """
    # enforce bounds on limit
    limit = max(1, min(limit, 500))

    stmt = (
        select(
            McpUnauthAccess.server_id,
            McpServerRegistry.name.label("server_name"),
            McpServerRegistry.risk_tier,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
        )
        .outerjoin(
            McpServerRegistry,
            McpUnauthAccess.server_id == McpServerRegistry.server_id,
        )
    )

    if server_id is not None:
        stmt = stmt.where(McpUnauthAccess.server_id == server_id)

    if unauth_access is not None:
        stmt = stmt.where(McpUnauthAccess.unauth_access == unauth_access)

    stmt = stmt.order_by(
        desc(McpUnauthAccess.updated_at).nulls_last(),
        asc(McpUnauthAccess.server_id),
    ).limit(limit)

    rows = db.execute(stmt).all()
    entries: List[Dict[str, Any]] = []
    for (
        s_id,
        s_name,
        risk_tier,
        url,
        unauth,
        source,
        updated_at,
    ) in rows:
        entries.append(
            {
                "server_id": s_id,
                "server_name": s_name,
                "risk_tier": risk_tier,
                "url": url,
                "unauth_access": unauth,
                "source": source,
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
        )
    return len(entries), entries


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite engine for isolated testing
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Seed data
    with SessionLocal() as session:
        # Registry entry (will be joined)
        registry_row = McpServerRegistry(
            server_id="srv-001",
            name="Test Server",
            risk_tier="high",
            verdict="unknown",
        )
        session.add(registry_row)

        # Unauth access rows
        ua_rows = [
            McpUnauthAccess(
                server_id="srv-001",
                url="https://example.com/1",
                unauth_access="true",
                source="srcA",
            ),
            McpUnauthAccess(
                server_id="srv-002",
                url="https://example.com/2",
                unauth_access="false",
                source="srcB",
            ),
            McpUnauthAccess(
                server_id="srv-003",
                url="https://example.com/3",
                unauth_access="unknown",
                source="srcC",
            ),
        ]
        session.add_all(ua_rows)
        session.commit()

        # 1️⃣  Default query – expect 3 rows
        cnt, ents = unauth_access_index(session)
        assert cnt == 3, f"expected 3 rows, got {cnt}"
        # matched row should have server_name & risk_tier
        matched = next(e for e in ents if e["server_id"] == "srv-001")
        assert matched["server_name"] == "Test Server"
        assert matched["risk_tier"] == "high"
        # unmatched rows should have nulls for joined fields
        for e in ents:
            if e["server_id"] != "srv-001":
                assert e["server_name"] is None
                assert e["risk_tier"] is None

        # 2️⃣  Filter by server_id – expect exactly 1 row
        cnt_f, ents_f = unauth_access_index(session, server_id="srv-002")
        assert cnt_f == 1, f"filter by server_id returned {cnt_f} rows"
        assert ents_f[0]["server_id"] == "srv-002"

        print("PASS")