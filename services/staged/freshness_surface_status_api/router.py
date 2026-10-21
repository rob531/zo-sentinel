from fastapi import APIRouter, Depends

from app.db import get_session

from .logic import surface_freshness

router = APIRouter(prefix="/api")


@router.get("/freshness/surfaces")
def freshness_surfaces(
    stale_after_seconds: int = 3600,
    session=Depends(get_session),
):
    return surface_freshness(
        session=session,
        stale_after_seconds=stale_after_seconds,
    )


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE freshness_stats (
                    surface VARCHAR NOT NULL,
                    payload TEXT,
                    computed_at VARCHAR,
                    compute_seconds FLOAT
                )
                """
            )
        )
        now = datetime.utcnow().replace(microsecond=0)
        connection.execute(
            text(
                """
                INSERT INTO freshness_stats
                    (surface, payload, computed_at, compute_seconds)
                VALUES
                    (:surface, :payload, :computed_at, :compute_seconds)
                """
            ),
            [
                {
                    "surface": "recent",
                    "payload": '{"status": "ok"}',
                    "computed_at": now.isoformat(),
                    "compute_seconds": 1.5,
                },
                {
                    "surface": "stale",
                    "payload": '{"status": "old"}',
                    "computed_at": (now - timedelta(days=2)).isoformat(),
                    "compute_seconds": 12.0,
                },
                {
                    "surface": "missing",
                    "payload": None,
                    "computed_at": None,
                    "compute_seconds": None,
                },
            ],
        )

    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = lambda: session

    try:
        with TestClient(local_app) as client:
            response = client.get("/api/freshness/surfaces")
            response.raise_for_status()
            result = response.json()

            assert result["count"] == 3
            assert len(result["surfaces"]) == 3
            assert any(surface["stale"] for surface in result["surfaces"])

            recent = next(
                surface for surface in result["surfaces"] if surface["surface"] == "recent"
            )
            assert recent["compute_seconds"] == 1.5
            assert isinstance(recent["payload"], dict)

            for surface in result["surfaces"]:
                if surface["computed_at"] is not None:
                    datetime.fromisoformat(surface["computed_at"].replace("Z", "+00:00"))
    finally:
        session.close()
        engine.dispose()

    print("PASS")