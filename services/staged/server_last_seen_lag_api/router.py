"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import last_seen_lag_buckets, fetch_lag_rows

router = APIRouter(prefix="/api", tags=["server_last_seen_lag"])


class Bucket(BaseModel):
    bucket: str
    count: int


class StaleServer(BaseModel):
    server_id: str = Field(..., alias="server_id")
    name: str
    risk_tier: str
    days_since_seen: int


class LastSeenLagResponse(BaseModel):
    days: int
    generated_at: str
    buckets: List[Bucket]
    stale_servers: List[StaleServer]


@router.get(
    "/servers/last-seen-lag",
    response_model=LastSeenLagResponse,
    summary="Get lag buckets and stale servers",
)
def get_last_seen_lag(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
) -> LastSeenLagResponse:
    """Return lag bucket counts and a list of stale servers.

    * `days` – look‑back window for the query (clamped to 1‑365).
    * `generated_at` – ISO‑8601 timestamp of response generation.
    * `buckets` – fixed bucket labels with counts.
    * `stale_servers` – up to 100 servers sorted by days since last seen.
    """
    # Clamp days (FastAPI already validates, but keep defensive)
    days = max(1, min(days, 365))

    # Retrieve raw rows from the DB.  The logic function may accept the
    # session as the first argument or not – try both signatures.
    try:
        rows = fetch_lag_rows(db, days)  # type: ignore[arg-type]
    except TypeError:
        rows = fetch_lag_rows(days)  # type: ignore[call-arg]

    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    now_iso = now.isoformat()

    # Compute bucket histogram via pure‑logic helper.
    bucket_dict = last_seen_lag_buckets(now_iso, rows)

    bucket_order = ["0-1d", "2-7d", "8-30d", "31-90d", "90d+"]
    buckets = [
        Bucket(bucket=label, count=int(bucket_dict.get(label, 0)))
        for label in bucket_order
    ]

    # Build stale‑servers list.
    stale: List[dict] = []
    for row in rows:
        last_seen_val = row.get("last_seen")
        if last_seen_val is None:
            continue
        if isinstance(last_seen_val, str):
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_val)
            except Exception:
                continue
        else:
            last_seen_dt = last_seen_val
        # Ensure timezone‑aware comparison.
        if last_seen_dt.tzinfo is None:
            last_seen_dt = last_seen_dt.replace(tzinfo=timezone.utc)
        days_since = (now - last_seen_dt).days
        stale.append(
            {
                "server_id": str(row.get("server_id")),
                "name": str(row.get("name", "")),
                "risk_tier": str(row.get("risk_tier", "")),
                "days_since_seen": days_since,
            }
        )

    stale_sorted = sorted(stale, key=lambda x: x["days_since_seen"], reverse=True)[:100]
    stale_servers = [StaleServer(**s) for s in stale_sorted]

    return LastSeenLagResponse(
        days=days,
        generated_at=now_iso,
        buckets=buckets,
        stale_servers=stale_servers,
    )