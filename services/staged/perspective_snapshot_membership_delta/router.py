from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Perspective, PerspectiveEvent, PerspectiveSnapshot

from .logic import get_snapshot_delta as _get_snapshot_delta

router = APIRouter(prefix="/api")


@router.get("/perspectives/{perspective_id}/snapshot-delta")
def get_perspective_snapshot_delta(
    perspective_id: str,
    session: Session = Depends(get_session),
):
    return _get_snapshot_delta(session, perspective_id)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    Perspective.__table__.create(engine)
    PerspectiveEvent.__table__.create(engine)
    PerspectiveSnapshot.__table__.create(engine)

    with session_factory() as session:
        session.add_all(
            [
                Perspective(
                    id="perspective-1",
                    name="Perspective One",
                    created_by="self-test",
                    facet_filters={},
                ),
                Perspective(
                    id="perspective-2",
                    name="Perspective Two",
                    created_by="self-test",
                    facet_filters={},
                ),
                PerspectiveSnapshot(
                    id=1,
                    perspective_id="perspective-1",
                    membership=["server-a", "server-b"],
                ),
                PerspectiveSnapshot(
                    id=2,
                    perspective_id="perspective-1",
                    membership=["server-b", "server-c"],
                ),
                PerspectiveEvent(
                    id=1,
                    perspective_id="perspective-1",
                    server_id="server-a",
                    change_type="tier_change",
                    old_tier="trusted",
                    new_tier="untrusted",
                ),
                PerspectiveEvent(
                    id=2,
                    perspective_id="perspective-1",
                    server_id="server-c",
                    change_type="tier_change",
                    old_tier="untrusted",
                    new_tier="trusted",
                ),
            ]
        )
        session.commit()

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/perspectives/perspective-1/snapshot-delta")
        assert response.status_code == 200
        payload = response.json()
        assert payload["perspective_id"] == "perspective-1"
        assert payload["from_snapshot_id"] == 1
        assert payload["to_snapshot_id"] == 2
        assert payload["added"] == ["server-c"]
        assert payload["removed"] == ["server-a"]
        assert payload["added_count"] == 1
        assert payload["removed_count"] == 1
        assert payload["event_count"] == 2

    engine.dispose()


if __name__ == "__main__":
    _run_self_test()
    print("PASS")