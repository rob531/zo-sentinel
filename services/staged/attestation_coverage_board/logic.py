"""logic.py -- attestation coverage computation for the attestation_coverage_board service.

Pure functions over a SQLAlchemy Session; the router can import and expose the
result via a Pydantic model. No writes, only reads.
"""

from __future__ import annotations

from typing import Dict, Any

from sqlalchemy import func, select, Table, MetaData
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _attestations_table(db: Session) -> Table:
    """Reflect the ``mcp_attestations`` bus table using the session's engine."""
    metadata = MetaData()
    return Table("mcp_attestations", metadata, autoload_with=db.get_bind())


def attestation_coverage(db: Session) -> Dict[str, Any]:
    """Return coverage statistics over the server registry.

    The result contains:
        - total_servers: total rows in ``mcp_server_registry``.
        - attested_servers: distinct server IDs that appear in ``mcp_attestations``.
        - coverage_pct: percentage (rounded to 2 decimals) of attested servers.
        - by_status: mapping of each distinct ``status`` value to its count.
        - oldest_at: earliest attestation timestamp (or ``None``).
        - newest_at: latest attestation timestamp (or ``None``).
    """
    # total servers
    total = db.execute(select(func.count()).select_from(McpServerRegistry)).scalar_one()
    total = int(total or 0)

    # reflected attestations table
    attest_tbl = _attestations_table(db)

    # distinct attested servers
    attested = db.execute(
        select(func.count(func.distinct(attest_tbl.c.server_id)))
    ).scalar_one()
    attested = int(attested or 0)

    # coverage percentage
    coverage_pct = round((attested / total) * 100, 2) if total else 0.0

    # breakdown by status (fallback to 'kind' if 'status' missing)
    status_col = (
        attest_tbl.c.status
        if "status" in attest_tbl.c
        else attest_tbl.c.kind
        if "kind" in attest_tbl.c
        else None
    )
    by_status: Dict[str, int] = {}
    if status_col is not None:
        rows = db.execute(
            select(status_col, func.count())
            .group_by(status_col)
        ).all()
        for status, cnt in rows:
            key = str(status) if status is not None else "unknown"
            by_status[key] = int(cnt)

    # timestamps (fallback to 'attested_at' -> 'timestamp')
    ts_col = (
        attest_tbl.c.attested_at
        if "attested_at" in attest_tbl.c
        else attest_tbl.c.timestamp
        if "timestamp" in attest_tbl.c
        else None
    )
    oldest_at = newest_at = None
    if ts_col is not None:
        oldest_at = db.execute(select(func.min(ts_col))).scalar_one()
        newest_at = db.execute(select(func.max(ts_col))).scalar_one()

    return {
        "total_servers": total,
        "attested_servers": attested,
        "coverage_pct": coverage_pct,
        "by_status": by_status,
        "oldest_at": oldest_at,
        "newest_at": newest_at,
    }


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine, Column, Integer, String, DateTime
    from sqlalchemy.orm import sessionmaker

    # In‑memory SQLite for the test
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    SessionLocal = sessionmaker(bind=engine, future=True)

    # Create the real registry table via its ORM definition
    McpServerRegistry.__table__.create(engine)

    # Create a minimal ``mcp_attestations`` table matching the test expectations
    meta = MetaData()
    attest_tbl = Table(
        "mcp_attestations",
        meta,
        Column("id", Integer, primary_key=True),
        Column("server_id", String, nullable=False),
        Column("status", String, nullable=True),
        Column("attested_at", DateTime, nullable=True),
    )
    meta.create_all(engine)

    # Seed data
    with SessionLocal() as db:
        # three servers
        db.add_all(
            [
                McpServerRegistry(server_id="s1", name="srv1", registry_source="test"),
                McpServerRegistry(server_id="s2", name="srv2", registry_source="test"),
                McpServerRegistry(server_id="s3", name="srv3", registry_source="test"),
            ]
        )
        # two attestations
        from datetime import datetime, timedelta

        now = datetime.utcnow()
        db.execute(
            attest_tbl.insert(),
            [
                {
                    "server_id": "s1",
                    "status": "good",
                    "attested_at": now - timedelta(days=2),
                },
                {
                    "server_id": "s2",
                    "status": "bad",
                    "attested_at": now,
                },
            ],
        )
        db.commit()

        # run the logic
        result = attestation_coverage(db)

    assert result["total_servers"] == 3, "total_servers mismatch"
    assert result["attested_servers"] == 2, "attested_servers mismatch"
    expected_pct = round((2 / 3) * 100, 2)
    assert result["coverage_pct"] == expected_pct, "coverage_pct mismatch"
    print("PASS")