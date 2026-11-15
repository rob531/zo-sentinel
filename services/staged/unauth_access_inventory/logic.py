"""logic.py -- unauth_access_inventory service data/computation layer.

Provides a pure function that queries the real app models via a SQLAlchemy
Session. The router can import and call this function; the __main__ block
exercises it against an in‑memory SQLite database to satisfy the contract.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpServerRegistry, McpUnauthAccess


def list_unauth_access(
    db: Session,
    source: Optional[str] = None,
    min_confidence: Optional[float] = None,
) -> Dict[str, Any]:
    """Return unauth‑access inventory.

    The result is a mapping with:
        - ``count``: total rows returned (max 500)
        - ``items``: list of dicts containing the joined columns

    Rows are left‑joined from ``mcp_unauth_access`` to
    ``mcp_server_registry`` on ``server_id`` and ordered by
    ``updated_at`` descending.  An absolute limit of 500 rows is applied.
    Optional filters:
        * ``source`` – exact match on ``McpUnauthAccess.source``.
        * ``min_confidence`` – rows whose registry ``confidence`` is at least
          the supplied value (rows without a matching registry are excluded
          when this filter is used).
    """
    stmt = (
        select(
            McpUnauthAccess.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpUnauthAccess.url,
            McpUnauthAccess.unauth_access,
            McpUnauthAccess.source,
            McpUnauthAccess.updated_at,
        )
        .select_from(
            McpUnauthAccess.__table__.outerjoin(
                McpServerRegistry,
                McpUnauthAccess.server_id == McpServerRegistry.server_id,
            )
        )
    )

    if source is not None:
        stmt = stmt.where(McpUnauthAccess.source == source)

    if min_confidence is not None:
        stmt = stmt.where(McpServerRegistry.confidence >= min_confidence)

    stmt = stmt.order_by(desc(McpUnauthAccess.updated_at)).limit(500)

    rows = db.execute(stmt).all()

    items: List[Dict[str, Any]] = []
    for (
        server_id,
        name,
        risk_tier,
        url,
        unauth_access,
        src,
        updated_at,
    ) in rows:
        items.append(
            {
                "server_id": server_id,
                "name": name,
                "risk_tier": risk_tier,
                "url": url,
                "unauth_access": unauth_access,
                "source": src,
                "updated_at": updated_at,
            }
        )

    return {"count": len(items), "items": items}


# --------------------------------------------------------------------------- #
# Self‑test executed when the module is run directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the self‑test.
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, future=True)

    with SessionLocal() as session:
        # Registry entry that will match one unauth‑access row.
        registry_row = McpServerRegistry(
            server_id="srv-001",
            name="Test Server",
            risk_tier="high",
            confidence=0.9,
        )
        session.add(registry_row)

        # Three unauth‑access rows: one with a matching registry, two without.
        ua1 = McpUnauthAccess(
            server_id="srv-001",
            url="https://example.com/secret",
            unauth_access="read",
            source="scanner",
        )
        ua2 = McpUnauthAccess(
            server_id="srv-002",
            url="https://example.org/open",
            unauth_access="write",
            source="scanner",
        )
        ua3 = McpUnauthAccess(
            server_id="srv-003",
            url="https://example.net/public",
            unauth_access="read",
            source="manual",
        )
        session.add_all([ua1, ua2, ua3])
        session.commit()

        result = list_unauth_access(session)

        assert result["count"] == 3, f"expected 3 rows, got {result['count']}"
        # Verify that the matching registry data appears only for srv-001.
        for item in result["items"]:
            if item["server_id"] == "srv-001":
                assert item["name"] == "Test Server"
                assert item["risk_tier"] == "high"
            else:
                assert item["name"] is None
                assert item["risk_tier"] is None

        print("PASS")