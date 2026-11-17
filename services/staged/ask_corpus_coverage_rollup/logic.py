from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


__all__ = [
    "get_coverage_rollup",
    "get_corpus_coverage_rollup",
    "get_corpus_coverage",
    "coverage_rollup",
    "get_rollup",
]


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _build_coverage_rollup(session: Session) -> Dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            McpServerRegistry.verdict,
            AskCorpusDoc.indexed_at,
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            AskCorpusDoc,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
    )
    rows = session.execute(statement).all()

    latest_indexed: Dict[str, datetime] = {}
    indexed_server_ids = set()
    stale_index_count = 0

    for server_id, _name, _risk_tier, _verdict, indexed_at in rows:
        if indexed_at is None:
            continue

        indexed_server_ids.add(server_id)
        normalized = _as_utc(indexed_at)
        if normalized is None:
            continue

        previous = latest_indexed.get(server_id)
        if previous is None or normalized > previous:
            latest_indexed[server_id] = normalized

        if normalized < cutoff:
            stale_index_count += 1

    total_servers = len(rows)
    indexed_servers = len(indexed_server_ids)
    coverage_pct = (
        round((indexed_servers / total_servers) * 100, 2)
        if total_servers
        else 0.0
    )

    by_risk_tier: Dict[str, int] = {}
    for server_id, _name, risk_tier, _verdict, _indexed_at in rows:
        if server_id not in indexed_server_ids:
            continue
        tier = risk_tier or "unknown"
        by_risk_tier[tier] = by_risk_tier.get(tier, 0) + 1

    gaps: List[Dict[str, Any]] = []
    for server_id, name, risk_tier, _verdict, indexed_at in rows:
        normalized = latest_indexed.get(server_id)
        is_stale = normalized is not None and normalized < cutoff
        if normalized is not None and not is_stale:
            continue

        last_indexed = normalized.isoformat() if normalized is not None else None
        gaps.append(
            {
                "server_id": server_id,
                "name": name,
                "risk_tier": risk_tier,
                "last_indexed": last_indexed,
            }
        )

    gaps.sort(key=lambda item: (str(item.get("risk_tier") or ""), str(item.get("name") or "")))

    return {
        "total_servers": total_servers,
        "indexed_servers": indexed_servers,
        "coverage_pct": coverage_pct,
        "stale_index_count": stale_index_count,
        "by_risk_tier": by_risk_tier,
        "top_gaps": gaps[:50],
    }


def get_coverage_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return _build_coverage_rollup(session)


def get_corpus_coverage_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return _build_coverage_rollup(session)


def get_corpus_coverage(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return _build_coverage_rollup(session)


def coverage_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return _build_coverage_rollup(session)


def get_rollup(session: Session = Depends(get_session)) -> Dict[str, Any]:
    return _build_coverage_rollup(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import BaseModel
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    class CoverageResponse(BaseModel):
        total_servers: int
        indexed_servers: int
        coverage_pct: float
        stale_index_count: int
        by_risk_tier: dict
        top_gaps: list

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    AskCorpusDoc.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    now = datetime.now(timezone.utc)
    with testing_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-a",
                    name="Server A",
                    risk_tier="high",
                    verdict="approved",
                ),
                McpServerRegistry(
                    server_id="server-b",
                    name="Server B",
                    risk_tier="medium",
                    verdict="approved",
                ),
                McpServerRegistry(
                    server_id="server-c",
                    name="Server C",
                    risk_tier="low",
                    verdict="approved",
                ),
                AskCorpusDoc(
                    server_id="server-a",
                    content_hash="hash-a",
                    indexed_at=now,
                    snippet="A",
                    terms="a",
                ),
                AskCorpusDoc(
                    server_id="server-b",
                    content_hash="hash-b",
                    indexed_at=now,
                    snippet="B",
                    terms="b",
                ),
            ]
        )
        session.commit()

    self_app = FastAPI()

    def override_get_session():
        with testing_session() as session:
            yield session

    self_app.dependency_overrides[get_session] = override_get_session

    @self_app.get(
        "/api/ask/corpus/coverage",
        response_model=CoverageResponse,
    )
    def coverage_endpoint(result: dict = Depends(get_coverage_rollup)):
        return result

    response = TestClient(self_app).get("/api/ask/corpus/coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 3
    assert payload["indexed_servers"] == 2
    assert 60 <= payload["coverage_pct"] <= 70
    assert any(gap["server_id"] == "server-c" for gap in payload["top_gaps"])
    print("PASS")