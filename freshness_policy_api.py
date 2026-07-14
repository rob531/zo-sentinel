"""freshness_policy_api.py -- the public, auditable statement of THE LINE.

GET /freshness/policy answers, without credentials:
  - what the declared SLA is,
  - whether the corpus is currently breaching it,
  - and WHICH surface classes fail closed vs fail visible.

This is a PUBLIC surface: it always serves, even (especially) when the answer
is "we are stale". Publishing the breach is the honest move; hiding it behind
auth is how a moat rots for 11 days unnoticed (2026-07-14).
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from freshness_gate import fleet_freshness, sla_days

router = APIRouter(tags=["freshness"])

ENFORCEMENT = {
    "keyed": {
        "policy": "fail_closed",
        "on_stale": "503 stale_data",
        "surfaces": ["badge_api", "claim_flow", "webhooks",
                     "api_key_authenticated", "signed_scan_output"],
    },
    "public": {
        "policy": "fail_visible",
        "on_stale": "200 with freshness label",
        "surfaces": ["/freshness", "/freshness/policy", "score_views",
                     "search", "/perspectives", "/ask"],
    },
}


@router.get("/freshness/policy")
def freshness_policy(db: Session = Depends(get_session)) -> dict:
    fleet = fleet_freshness(db)
    return {
        "sla_days": sla_days(),
        "corpus": fleet,
        "enforcement": ENFORCEMENT,
        "line": ("no signed/keyed surface on stale data; "
                 "public surfaces serve, labelled honestly"),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
