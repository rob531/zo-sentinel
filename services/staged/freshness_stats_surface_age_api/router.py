from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone

from app.db import get_session
from app.models import FreshnessStat

from .logic import get_surface_age_stats

router = APIRouter(prefix="/api")


@router.get("/freshness/surface-age")
def surface_age_stats(session: Session = Depends(get_session)):
    return get_surface_age_stats(session)


def _self_test() -> None:
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    seeded = [
        {
            "surface": "alpha",
            "payload": {"kind": "alpha", "value": 1},
            "computed_at": now - timedelta(seconds=60),
            "compute_seconds": 1.25,
        },
        {
            "surface": "beta",
            "payload": {"kind": "beta", "value": 2},
            "computed_at": now - timedelta(seconds=120),
            "compute_seconds": 2.5,
        },
        {
            "surface": "gamma",
            "payload": {"kind": "gamma", "value": 3},
            "computed_at": now - timedelta(seconds=180),
            "compute_seconds": 3.75,
        },
    ]

    with session_factory() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface=row["surface"],
                    payload=row["payload"],
                    computed_at=row["computed_at"],
                    compute_seconds=row["compute_seconds"],
                )
                for row in seeded
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/freshness/surface-age")

    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    by_surface = {row["surface"]: row for row in body["surfaces"]}
    assert by_surface["alpha"]["payload"] == seeded[0]["payload"]
    assert all(row["age_seconds"] >= 0 for row in body["surfaces"])
    assert [row["surface"] for row in body["surfaces"]] == ["alpha", "beta", "gamma"]

    print("PASS")


if __name__ == "__main__":
    _self_test()