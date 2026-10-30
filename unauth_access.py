"""unauth_access.py -- resolve the live-probe EXPOSURE signal for one server.

WAF-robust exposure (OPEN / GATED / UNREACHABLE) lives in the ISOLATED mcp_unauth_access
table, NOT in mcp_llm_axis_scores -- the per-server overview serves newest-scored_at, so a
score row there would be a live serving change (POSTMORTEM_20261030, a ~9%-sub incident).
Consume the signal ONLY through this resolver.

Design hardened per the code-review of #12255:
  * OPT-IN: default OFF (matches the repo's flag convention: SFT_BATCH_ENABLED /
    ARTIFACT_INGESTOR_ENABLED / PR_PUBLISHER_ENABLED are all default-off). Arm with
    UNAUTH_ACCESS_ENABLED in {1,true,yes,on} (case/space-insensitive). Robust kill-switch,
    unlike the first cut's brittle exact-"0" check (false/off/no now correctly disable).
  * FAIL-OPEN but NOT SILENT: on any DB error, roll back (so a doomed transaction can't
    poison the request's session) and LOG a warning -- a prod-only schema/identifier error
    must be visible, never permanently dark ("done-flags that lied" is a named repo scar).
  * FACTUAL, not gated: exposure is a measured fact (did it serve tools unauthenticated?),
    not an inferred risk tier, so it is deliberately NOT run through trust_gate -- gating it
    would hide a true property of an official publisher.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy import select

from app.models import McpUnauthAccess

_LOG = logging.getLogger("unauth_access")
_ENABLE = {"1", "true", "yes", "on"}


def unauth_access_enabled() -> bool:
    return os.environ.get("UNAUTH_ACCESS_ENABLED", "").strip().lower() in _ENABLE


def resolve_unauth_access(db, server_id: str) -> Optional[str]:
    """OPEN/GATED/UNREACHABLE for server_id, or None (feature off / no row / error)."""
    if not unauth_access_enabled():
        return None
    try:
        row = db.execute(
            select(McpUnauthAccess.unauth_access)
            .where(McpUnauthAccess.server_id == server_id)
            .limit(1)
        ).first()
        return row[0] if row else None
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        _LOG.warning("unauth_access read failed for server_id=%s (feature dark; investigate)",
                     server_id, exc_info=True)
        return None


if __name__ == "__main__":  # hermetic 3-pole self-test (no network, no :8772)
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    McpUnauthAccess.__table__.create(eng)
    db = sessionmaker(bind=eng)()
    db.add(McpUnauthAccess(server_id="s1", unauth_access="OPEN"))
    db.commit()

    # Pole 1 -- flag OFF (default) -> None
    os.environ.pop("UNAUTH_ACCESS_ENABLED", None)
    assert resolve_unauth_access(db, "s1") is None, "flag-off must be None"

    # Pole 2 -- armed + present -> value; absent row -> None; robust disable values
    os.environ["UNAUTH_ACCESS_ENABLED"] = "1"
    assert resolve_unauth_access(db, "s1") == "OPEN", "armed+present must be OPEN"
    assert resolve_unauth_access(db, "nope") is None, "armed+absent-row must be None"
    for off in ("0", "false", "off", "no", "  "):
        os.environ["UNAUTH_ACCESS_ENABLED"] = off
        assert resolve_unauth_access(db, "s1") is None, f"{off!r} must disable"
    os.environ["UNAUTH_ACCESS_ENABLED"] = "TRUE"   # case-insensitive enable
    assert resolve_unauth_access(db, "s1") == "OPEN"

    # Pole 3 -- armed + table ABSENT -> fail-open None (no raise), exercises rollback+log path
    db2 = sessionmaker(bind=create_engine("sqlite://", connect_args={"check_same_thread": False},
                                          poolclass=StaticPool))()
    os.environ["UNAUTH_ACCESS_ENABLED"] = "1"
    assert resolve_unauth_access(db2, "s1") is None, "missing table must fail open to None"

    print("PASS")
