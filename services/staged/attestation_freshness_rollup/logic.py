"""logic.py -- attestation_freshness_rollup service data/computation layer.

Provides a single pure function that operates over a SQLAlchemy Session.
The router (services/staged/attestation_freshness_rollup/router.py) injects the
session via Depends(get_session) and returns the dict produced here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Mapping, Optional

from sqlalchemy import MetaData, Table, select, func
from sqlalchemy.orm import Session

from app.models import McpServerRegistry


def _reflect_attestations_table(db: Session) -> Table:
    """Reflect the ``mcp_attestations`` table from the current engine."""
    metadata = MetaData()
    return Table("mcp_attestations", metadata, autoload_with=db.get_bind())


def _expiry_column(table: Table) -> Optional[Table.c]:
    """Return the first column that looks like an expiry timestamp, or None."""
    for name in ("expiry", "expiry_at", "expires_at", "expiration"):
        if name in table.c:
            return table.c[name]
    return None


def attestation_freshness_stats(db: Session) -> Mapping[str, object]:
    """
    Compute attestation freshness statistics.

    Returns a mapping compatible with the API contract:

    {
        "generated_at": "<ISO8601 UTC>",
        "total": int,
        "expiring_30d": int,
        "expired": int,
        "unattested_servers": int,
        "by_tier": {
            "<tier>": {
                "total": int,
                "expiring_30d": int,
                "expired": int,
                "unattested": int,
            },
            ...
        },
    }
    """
    now = datetime.now(timezone.utc)
    in_30d = now + timedelta(days=30)

    # ------------------------------------------------------------------ #
    # 1. Load attestations (reflecting the table to avoid missing model)
    # ------------------------------------------------------------------ #
    attest_table = _reflect_attestations_table(db)
    server_id_col = attest_table.c.get("server_id")
    expiry_col = _expiry_column(attest_table)

    if server_id_col is None:
        # Without a server identifier we cannot compute anything sensible.
        raise RuntimeError("mcp_attestations table lacks a 'server_id' column")

    # Build the SELECT statement dynamically based on column availability.
    if expiry_col is not None:
        stmt = select(server_id_col, expiry_col)
    else:
        stmt = select(server_id_col)

    att_rows = db.execute(stmt).all()

    # Map server_id -> expiry (or None if column missing)
    att_expiries: Dict[int, Optional[datetime]] = {}
    for row in att_rows:
        sid = row[0]
        exp = row[1] if expiry_col is not None else None
        att_expiries[sid] = exp

    total_attestations = len(att_expiries)

    # ------------------------------------------------------------------ #
    # 2. Load server registry
    # ------------------------------------------------------------------ #
    reg_stmt = select(
        McpServerRegistry.server_id,
        McpServerRegistry.risk_tier,
    )
    reg_rows = db.execute(reg_stmt).all()

    # Build structures for tier grouping
    tier_to_servers: Dict[Optional[str], set[int]] = {}
    for server_id, tier in reg_rows:
        tier_to_servers.setdefault(tier, set()).add(server_id)

    # ------------------------------------------------------------------ #
    # 3. Compute global counters
    # ------------------------------------------------------------------ #
    expired = 0
    expiring_30d = 0
    if expiry_col is not None:
        for exp in att_expiries.values():
            if exp is None:
                continue
            if exp <= now:
                expired += 1
            elif now < exp <= in_30d:
                expiring_30d += 1

    # Servers that have a registry entry but no attestation
    unattested_servers = 0
    for tier_servers in tier_to_servers.values():
        unattested_servers += sum(1 for sid in tier_servers if sid not in att_expiries)

    # ------------------------------------------------------------------ #
    # 4. Compute per‑tier statistics
    # ------------------------------------------------------------------ #
    by_tier: Dict[str, Dict[str, int]] = {}
    for tier, servers in tier_to_servers.items():
        tier_key = tier or "unknown"
        tier_total = 0
        tier_expired = 0
        tier_expiring = 0
        tier_unattested = 0

        for sid in servers:
            if sid in att_expiries:
                tier_total += 1
                exp = att_expiries[sid]
                if expiry_col is not None and exp is not None:
                    if exp <= now:
                        tier_expired += 1
                    elif now < exp <= in_30d:
                        tier_expiring += 1
            else:
                tier_unattested += 1

        by_tier[tier_key] = {
            "total": tier_total,
            "expiring_30d": tier_expiring,
            "expired": tier_expired,
            "unattested": tier_unattested,
        }

    # ------------------------------------------------------------------ #
    # 5. Assemble final payload
    # ------------------------------------------------------------------ #
    result: Dict[str, object] = {
        "generated_at": now.isoformat(),
        "total": total_attestations,
        "expiring_30d": expiring_30d,
        "expired": expired,
        "unattested_servers": unattested_servers,
        "by_tier": by_tier,
    }
    return result