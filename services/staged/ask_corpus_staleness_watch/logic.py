from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc


router = APIRouter()


def _utc_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp_string(value: Any) -> Optional[str]:
    parsed = _utc_timestamp(value)
    if parsed is None:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def _terms_count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def corpus_staleness(
    db: Session,
    stale_days: int = 7,
    limit: int = 50,
) -> dict:
    """Report the age and coverage of the indexed ask corpus."""
    rows = db.execute(
        select(
            AskCorpusDoc.server_id,
            AskCorpusDoc.snippet,
            AskCorpusDoc.terms,
            AskCorpusDoc.content_hash,
            AskCorpusDoc.indexed_at,
        )
    ).all()

    now = datetime.now(timezone.utc)
    stale = []
    indexed_timestamps = []

    for server_id, snippet, terms, _content_hash, indexed_at in rows:
        indexed = _utc_timestamp(indexed_at)
        if indexed is None:
            continue

        indexed_timestamps.append(indexed)
        age_days = int((now - indexed).total_seconds() // 86400)
        if age_days >= stale_days:
            stale.append(
                {
                    "server_id": str(server_id),
                    "snippet": snippet or "",
                    "indexed_at": _timestamp_string(indexed_at),
                    "age_days": age_days,
                    "terms_count": _terms_count(terms),
                }
            )

    stale.sort(key=lambda item: item["age_days"], reverse=True)
    stale = stale[: max(0, limit)]

    total_docs = len(rows)
    stale_docs = len(stale)
    oldest = min(indexed_timestamps) if indexed_timestamps else None
    newest = max(indexed_timestamps) if indexed_timestamps else None

    return {
        "stale_days": int(stale_days),
        "summary": {
            "total_docs": int(total_docs),
            "stale_docs": int(stale_docs),
            "stale_pct": round(stale_docs * 100.0 / total_docs, 2)
            if total_docs
            else 0.0,
            "oldest_indexed_at": _timestamp_string(oldest),
            "newest_indexed_at": _timestamp_string(newest),
        },
        "stale": stale,
    }


@router.get("/ask/corpus-staleness")
def corpus_staleness_endpoint(
    db: Session = Depends(get_session),
    stale_days: int = Query(7, ge=0),
    limit: int = Query(50, ge=1),
) -> dict:
    return corpus_staleness(db=db, stale_days=stale_days, limit=limit)