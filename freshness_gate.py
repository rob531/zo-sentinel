"""freshness_gate.py -- THE LINE, enforced in code (CofC ruling 2026-07-14).

WHY THIS EXISTS
---------------
On 2026-07-14 the moat was found 11 days stale (newest_scored_at 2026-07-03)
against a 7-day SLA. Two failures, not one:

  1. The data was stale.
  2. NOTHING IN THE CODE KNEW OR CARED.

freshness_metadata_api shipped an ``is_fresh()`` helper that no caller ever
called, with a DEFAULT SLA OF 30 DAYS -- so an 11-day-old score was reported
"FRESH". A doctrine that isn't a runtime check isn't a doctrine, it's a wish.

This module is the single source of truth for score freshness. Council ruling
(FATHER, 2026-07-14), two surface classes, and the distinction is the whole
point:

  KEYED  -> FAIL CLOSED.   Signed / keyed / attestable surfaces (badge API,
                           claim flow, webhooks, API-key endpoints, signed
                           /scan output) MUST refuse to serve stale data.
                           503 + machine-readable reason. THE LINE.

  PUBLIC -> FAIL VISIBLE.  Unauthenticated read surfaces (/freshness, score
                           views, search, /perspectives, /ask) MUST keep
                           serving -- labelled honestly with age. A gate that
                           500s a live public read is WORSE than the staleness
                           it prevents. Public surfaces NEVER fail closed here.

The trust failure was never staleness. It was SILENT staleness.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from fastapi import Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

# The declared SLA. ONE number, shared by the gate and every surface that
# labels freshness, so the two can never drift apart again (they had: the
# watch said 7d, freshness_metadata_api said 30d).
DEFAULT_SLA_DAYS = 7
AGING_MULTIPLIER = 2  # > SLA but <= 2x SLA == "aging": still served, loudly

FRESH, AGING, STALE, NEVER_SCORED = "fresh", "aging", "stale", "never_scored"


class SurfaceClass(str, Enum):
    KEYED = "keyed"    # fail CLOSED
    PUBLIC = "public"  # fail VISIBLE


def sla_days() -> int:
    """Env-tunable so ops can tighten without a deploy. Never < 1."""
    try:
        return max(1, int(os.environ.get("FRESHNESS_SLA_DAYS", DEFAULT_SLA_DAYS)))
    except (TypeError, ValueError):
        return DEFAULT_SLA_DAYS


def _now() -> datetime:
    # Postgres columns here are tz-naive (see memory: tz-naive PG); compare
    # like-for-like or every age is silently wrong by the UTC offset.
    return datetime.utcnow()


def _classify(age: Optional[timedelta], days: int) -> str:
    if age is None:
        return NEVER_SCORED
    if age <= timedelta(days=days):
        return FRESH
    if age <= timedelta(days=days * AGING_MULTIPLIER):
        return AGING
    return STALE


def score_age_days(db: Session, server_id: str,
                   now: Optional[datetime] = None) -> Optional[float]:
    """Age in days of the server's NEWEST score row. None == never scored.

    None is NOT zero and NOT fresh. Callers that conflate "unknown" with
    "fine" are the bug this module exists to prevent.
    """
    scored_at = db.scalar(
        select(func.max(McpLlmAxisScore.scored_at))
        .where(McpLlmAxisScore.server_id == server_id))
    if scored_at is None:
        return None
    return max(0.0, ((now or _now()) - scored_at).total_seconds() / 86400.0)


def freshness_envelope(db: Session, server_id: str,
                       now: Optional[datetime] = None) -> dict:
    """The honest label. Attach to any PUBLIC payload that carries a score."""
    days = sla_days()
    age = score_age_days(db, server_id, now=now)
    scored_at = db.scalar(
        select(func.max(McpLlmAxisScore.scored_at))
        .where(McpLlmAxisScore.server_id == server_id))
    return {
        "scored_at": scored_at.isoformat() if scored_at else None,
        "age_days": round(age, 2) if age is not None else None,
        "sla_days": days,
        "status": _classify(timedelta(days=age) if age is not None else None, days),
    }


def fleet_freshness(db: Session, now: Optional[datetime] = None) -> dict:
    """Corpus-level truth -- what the daily watch and /freshness/policy read."""
    days = sla_days()
    newest = db.scalar(select(func.max(McpLlmAxisScore.scored_at)))
    oldest = db.scalar(select(func.min(McpLlmAxisScore.scored_at)))
    age = None if newest is None else (now or _now()) - newest
    return {
        "newest_scored_at": newest.isoformat() if newest else None,
        "oldest_scored_at": oldest.isoformat() if oldest else None,
        "corpus_age_days": round(age.total_seconds() / 86400.0, 2) if age else None,
        "sla_days": days,
        "status": _classify(age, days),
        "breaching_sla": _classify(age, days) in (AGING, STALE, NEVER_SCORED),
    }


def is_fresh(db: Session, server_id: str, now: Optional[datetime] = None) -> bool:
    """True ONLY on fresh. aging, stale and never_scored all fail closed."""
    return freshness_envelope(db, server_id, now=now)["status"] == FRESH


def assert_fresh(db: Session, server_id: str,
                 surface_class: SurfaceClass = SurfaceClass.KEYED,
                 now: Optional[datetime] = None) -> dict:
    """THE gate. KEYED raises 503 on non-fresh; PUBLIC never raises.

    Returns the envelope either way, so PUBLIC callers can label with the
    same object they would have been refused for.
    """
    env = freshness_envelope(db, server_id, now=now)
    if surface_class is SurfaceClass.PUBLIC:
        return env                      # fail VISIBLE: always serves
    if env["status"] != FRESH:          # fail CLOSED: THE LINE
        raise HTTPException(
            status_code=503,
            detail={
                "error": "stale_data",
                "message": ("This surface is signed/keyed and refuses to serve "
                            "data older than its declared SLA."),
                "server_id": server_id,
                **env,
            },
        )
    return env


def require_fresh(server_id: str, db: Session = Depends(get_session)) -> dict:
    """FastAPI dependency for keyed routers:

        @router.get("/badge/{server_id}")
        def badge(server_id: str, fresh: dict = Depends(require_fresh)): ...

    The badge/claim surfaces are deliberately unmounted today. This gate lands
    FIRST so that on the day they mount, they cannot bypass it.
    """
    return assert_fresh(db, server_id, SurfaceClass.KEYED)
