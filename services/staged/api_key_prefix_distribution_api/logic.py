import hashlib
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey

router = APIRouter(prefix="/api", tags=["api-keys"])


def _fingerprint(key_hash: str) -> str:
    return hashlib.sha256(key_hash.encode("utf-8")).hexdigest()[:12]


@router.get("/api-keys/prefix-distribution")
def get_prefix_distribution(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    rows = session.execute(
        select(ApiKey.id, ApiKey.org_id, ApiKey.key_hash).order_by(ApiKey.id)
    ).all()

    fingerprints: Dict[str, List[int]] = {}
    org_counts: Counter = Counter()

    for key_id, org_id, key_hash in rows:
        fingerprint = _fingerprint(key_hash)
        fingerprints.setdefault(fingerprint, []).append(key_id)
        org_counts[org_id] += 1

    duplicate_groups = [
        {"fingerprint": fingerprint, "count": len(key_ids), "key_ids": key_ids}
        for fingerprint, key_ids in sorted(fingerprints.items())
        if len(key_ids) > 1
    ]

    return {
        "total_keys": len(rows),
        "distinct_fingerprints": len(fingerprints),
        "duplicate_groups": duplicate_groups,
        "by_org": [
            {"org_id": org_id, "count": count}
            for org_id, count in sorted(
                org_counts.items(), key=lambda item: (item[0] is None, item[0])
            )
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    raw_hashes = [
        "reused-secret-material",
        "reused-secret-material",
        "distinct-secret-material-one",
        "distinct-secret-material-two",
    ]
    with TestSession() as session:
        for index, key_hash in enumerate(raw_hashes, start=1):
            session.add(
                ApiKey(
                    id=index,
                    org_id=1 if index <= 3 else 2,
                    key_hash=key_hash,
                    label=f"key-{index}",
                    created_at=datetime.now(timezone.utc),
                )
            )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/api-keys/prefix-distribution")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_keys"] == 4, payload
    assert payload["distinct_fingerprints"] == 3, payload
    assert len(payload["duplicate_groups"]) == 1, payload
    assert payload["duplicate_groups"][0]["count"] == 2, payload

    serialized = response.text
    for raw_hash in raw_hashes:
        assert raw_hash not in serialized

    print("PASS")