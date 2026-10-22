from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import AskCorpusDoc, McpServerRegistry


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(indexed_at: datetime, now: datetime) -> int:
    elapsed = now - _as_utc(indexed_at)
    if elapsed.total_seconds() <= 0:
        return 0
    return int(elapsed.total_seconds() // 86400)


def get_stale_documents(
    session: Session,
    max_age_days: int = 30,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=max_age_days)

    rows = (
        session.query(AskCorpusDoc, McpServerRegistry)
        .outerjoin(
            McpServerRegistry,
            AskCorpusDoc.server_id == McpServerRegistry.server_id,
        )
        .filter(
            or_(
                AskCorpusDoc.indexed_at < cutoff,
                McpServerRegistry.server_id.is_(None),
            )
        )
        .all()
    )

    documents = []
    stale_count = 0
    orphaned_count = 0

    for document, registry in rows:
        indexed_at = _as_utc(document.indexed_at)
        is_stale = indexed_at < cutoff
        is_orphaned = registry is None or registry.server_id is None

        if is_stale:
            stale_count += 1
        if is_orphaned:
            orphaned_count += 1

        if is_orphaned and is_stale:
            reason = "orphaned; stale"
        elif is_orphaned:
            reason = "orphaned"
        else:
            reason = "stale"

        documents.append(
            {
                "server_id": document.server_id,
                "name": registry.name if registry is not None else None,
                "indexed_at": indexed_at.isoformat(),
                "age_days": _age_days(indexed_at, now),
                "content_hash": document.content_hash,
                "reason": reason,
            }
        )

    documents.sort(
        key=lambda item: (
            -item["age_days"],
            item["server_id"],
        )
    )

    return {
        "max_age_days": max_age_days,
        "stale_count": stale_count,
        "orphaned_count": orphaned_count,
        "documents": documents[:500],
    }


list_stale_documents = get_stale_documents
get_stale_corpus_documents = get_stale_documents
find_stale_documents = get_stale_documents


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)
    AskCorpusDoc.__table__.create(engine)

    db = TestingSession()
    now = datetime.now(timezone.utc)

    db.add_all(
        [
            McpServerRegistry(
                server_id="fresh-server",
                name="Fresh Server",
                verdict="safe",
                risk_tier="low",
            ),
            McpServerRegistry(
                server_id="stale-server",
                name="Stale Server",
                verdict="unknown",
                risk_tier="medium",
            ),
        ]
    )
    db.add_all(
        [
            AskCorpusDoc(
                server_id="fresh-server",
                snippet="fresh",
                terms="fresh",
                content_hash="fresh-hash",
                indexed_at=now - timedelta(days=1),
            ),
            AskCorpusDoc(
                server_id="stale-server",
                snippet="stale",
                terms="stale",
                content_hash="stale-hash",
                indexed_at=now - timedelta(days=90),
            ),
            AskCorpusDoc(
                server_id="orphan-one",
                snippet="orphan one",
                terms="orphan",
                content_hash="orphan-one-hash",
                indexed_at=now - timedelta(days=2),
            ),
            AskCorpusDoc(
                server_id="orphan-two",
                snippet="orphan two",
                terms="orphan",
                content_hash="orphan-two-hash",
                indexed_at=now - timedelta(days=3),
            ),
        ]
    )
    db.commit()

    that_app = FastAPI()

    @that_app.get("/api/ask/corpus/stale")
    def stale_endpoint(
        max_age_days: int = 30,
        session: Session = Depends(get_session),
    ):
        return get_stale_documents(session, max_age_days)

    def override_get_session():
        yield db

    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get(
        "/api/ask/corpus/stale",
        params={"max_age_days": 30},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["max_age_days"] == 30
    assert payload["stale_count"] == 1
    assert payload["orphaned_count"] == 2
    assert len(payload["documents"]) == 3
    assert all(document["reason"].strip() for document in payload["documents"])

    print("PASS")