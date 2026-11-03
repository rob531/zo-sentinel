"""logic.py -- corpus coverage computation over the real application database.

The service is read-only: corpus and registry rows are read through the
application SQLAlchemy session and all JSON handling uses the standard library.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Set

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


def _parse_terms(value: Any) -> Any:
    """Parse the terms JSON column without relying on a JSON database type."""
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, (list, dict)) else []
    return []


def _age_bucket(indexed_at: datetime | None, now: datetime) -> str:
    """Return the age bucket for an indexed_at timestamp."""
    if indexed_at is None:
        return "gt_72h"

    if indexed_at.tzinfo is None:
        indexed_at = indexed_at.replace(tzinfo=timezone.utc)
    else:
        indexed_at = indexed_at.astimezone(timezone.utc)

    age = (now - indexed_at).total_seconds()
    if age < 24 * 60 * 60:
        return "lt_24h"
    if age <= 72 * 60 * 60:
        return "h_24_72"
    return "gt_72h"


def get_coverage(db: Session) -> Dict[str, Any]:
    """Compute corpus coverage against the MCP server registry."""
    corpus_rows = db.execute(
        select(
            AskCorpusDoc.server_id,
            AskCorpusDoc.snippet,
            AskCorpusDoc.terms,
            AskCorpusDoc.content_hash,
            AskCorpusDoc.indexed_at,
        )
    ).all()
    registry_rows = db.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
    ).all()

    indexed_server_ids: Set[str] = set()
    age_buckets: Dict[str, int] = {
        "lt_24h": 0,
        "h_24_72": 0,
        "gt_72h": 0,
    }
    now = datetime.now(timezone.utc)

    for server_id, _snippet, terms, _content_hash, indexed_at in corpus_rows:
        indexed_server_ids.add(str(server_id))
        # Parse the JSON column at the data boundary even though terms do not
        # affect the aggregate coverage metrics.
        _parse_terms(terms)
        age_buckets[_age_bucket(indexed_at, now)] += 1

    registry_server_ids: Set[str] = set()
    missing: List[Dict[str, str]] = []
    for server_id, name, _risk_tier in registry_rows:
        registry_server_ids.add(str(server_id))
        if str(server_id) not in indexed_server_ids:
            missing.append(
                {
                    "server_id": str(server_id),
                    "name": str(name) if name is not None else "",
                }
            )

    missing.sort(key=lambda item: (item["name"].casefold(), item["server_id"]))
    registry_count = len(registry_server_ids)
    indexed_count = len(indexed_server_ids)
    coverage_pct = (
        float(indexed_count) / float(registry_count) * 100.0
        if registry_count
        else 0.0
    )

    return {
        "indexed": indexed_count,
        "registry": registry_count,
        "coverage_pct": coverage_pct,
        "missing": missing[:25],
        "age_buckets": age_buckets,
    }


# Keep the common service-layer entry-point names available to thin routers.
get_coverage_index = get_coverage
get_corpus_coverage = get_coverage
compute_coverage = get_coverage


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    AskCorpusDoc.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine)

    db = SessionLocal()
    now = datetime.now(timezone.utc)
    for server_id, name in (
        ("server-1", "Alpha"),
        ("server-2", "Bravo"),
        ("server-3", "Charlie"),
        ("server-4", "Delta"),
        ("server-5", "Echo"),
    ):
        db.add(
            McpServerRegistry(
                server_id=server_id,
                name=name,
                risk_tier="medium",
            )
        )
    for server_id in ("server-1", "server-2", "server-3"):
        db.add(
            AskCorpusDoc(
                server_id=server_id,
                snippet=f"snippet for {server_id}",
                terms=json.dumps(["example", server_id]),
                content_hash=f"hash-{server_id}",
                indexed_at=now,
            )
        )
    db.commit()

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = lambda: db

    @local_app.get("/coverage")
    def coverage(session: Session = Depends(get_session)):
        return get_coverage(session)

    with TestClient(local_app) as client:
        response = client.get("/coverage")
        assert response.status_code == 200
        result = response.json()
        assert result["indexed"] == 3
        assert result["registry"] == 5
        assert result["coverage_pct"] == 60.0
        assert len(result["missing"]) == 2
        assert result["age_buckets"]["lt_24h"] == 3

    db.close()
    print("PASS")