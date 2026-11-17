from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import ApiKey


router = APIRouter(prefix="/api")


def _fingerprint(key_hash: Any) -> str:
    value = key_hash.decode() if isinstance(key_hash, bytes) else str(key_hash)
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


@router.get("/api-keys/prefix-distribution")
def prefix_distribution(session: Session = Depends(get_session)) -> Dict[str, Any]:
    rows = session.execute(
        ApiKey.__table__.select().with_only_columns(
            ApiKey.id,
            ApiKey.org_id,
            ApiKey.key_hash,
        )
    ).all()

    groups: Dict[str, List[int]] = defaultdict(list)
    org_counts: Counter = Counter()

    for row in rows:
        fingerprint = _fingerprint(row.key_hash)
        groups[fingerprint].append(row.id)
        org_counts[row.org_id] += 1

    duplicate_groups = [
        {
            "fingerprint": fingerprint,
            "count": len(key_ids),
            "key_ids": sorted(key_ids),
        }
        for fingerprint, key_ids in groups.items()
        if len(key_ids) > 1
    ]
    duplicate_groups.sort(key=lambda group: (-group["count"], group["fingerprint"]))

    by_org = [
        {"org_id": org_id, "count": count}
        for org_id, count in sorted(
            org_counts.items(),
            key=lambda item: (str(item[0]) if item[0] is not None else "", item[0] is not None),
        )
    ]

    return {
        "total_keys": len(rows),
        "distinct_fingerprints": len(groups),
        "duplicate_groups": duplicate_groups,
        "by_org": by_org,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


app = FastAPI()
app.include_router(router)


def run() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE api_keys (
                    id INTEGER PRIMARY KEY,
                    org_id INTEGER,
                    key_hash TEXT NOT NULL,
                    label TEXT,
                    created_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO api_keys (id, org_id, key_hash, label, created_at)
                VALUES
                    (1, 10, 'raw-hash-a', 'first', '2024-01-01T00:00:00Z'),
                    (2, 10, 'raw-hash-b', 'second', '2024-01-01T00:00:01Z'),
                    (3, 20, 'raw-hash-a', 'reused', '2024-01-01T00:00:02Z'),
                    (4, 20, 'raw-hash-c', 'other', '2024-01-01T00:00:03Z')
                """
            )
        )

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/api-keys/prefix-distribution")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total_keys"] == 4
            assert payload["distinct_fingerprints"] == 3
            assert len(payload["duplicate_groups"]) == 1
            assert payload["duplicate_groups"][0]["count"] == 2
            assert len(payload["duplicate_groups"][0]["key_ids"]) == 2
            assert "raw-hash-a" not in response.text
            assert "raw-hash-b" not in response.text
            assert "raw-hash-c" not in response.text

        print("PASS")
    finally:
        engine.dispose()


if __name__ == "__main__":
    run()