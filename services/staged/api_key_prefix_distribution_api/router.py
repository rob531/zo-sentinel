from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey
from .logic import get_prefix_distribution

router = APIRouter()


@router.get("/api-keys/prefix-distribution")
def api_key_prefix_distribution(session: Session = Depends(get_session)):
    return get_prefix_distribution(session)


if __name__ == "__main__":
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ApiKey.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    raw_hashes = ["hash-a", "hash-b", "hash-a", "hash-c"]
    now = datetime.now(timezone.utc)
    for index, key_hash in enumerate(raw_hashes, start=1):
        testing_session.add(
            ApiKey(
                id=index,
                org_id=1 if index < 4 else 2,
                key_hash=key_hash,
                label=f"key-{index}",
                created_at=now,
            )
        )
    testing_session.commit()

    def override_get_session():
        yield testing_session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router, prefix="/api")

    response = TestClient(that_app).get("/api/api-keys/prefix-distribution")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_keys"] == 4, payload
    assert payload["distinct_fingerprints"] == 3, payload
    assert len(payload["duplicate_groups"]) == 1, payload
    assert payload["duplicate_groups"][0]["count"] == 2, payload
    assert all(raw_hash not in response.text for raw_hash in raw_hashes), response.text

    print("PASS")