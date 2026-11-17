from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_seen_gap_distribution

router = APIRouter(prefix="/api")


@router.get("/registry/seen-gap")
def seen_gap_distribution(session: Session = Depends(get_session)):
    return get_seen_gap_distribution(session)


if __name__ == "__main__":
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
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    now = datetime.utcnow()
    seeded = [
        ("seen-gap-0", now, now, now),
        ("seen-gap-1", now - timedelta(days=2), now - timedelta(days=1), now - timedelta(days=1)),
        ("seen-gap-8", now - timedelta(days=10), now - timedelta(days=2), now - timedelta(days=2)),
        ("seen-gap-30", now - timedelta(days=40), now - timedelta(days=10), now - timedelta(days=10)),
        ("seen-gap-90", now - timedelta(days=100), now - timedelta(days=20), now - timedelta(days=20)),
        ("seen-gap-365", now - timedelta(days=500), now - timedelta(days=100), now - timedelta(days=100)),
    ]
    for server_id, first_seen, last_seen, last_scanned in seeded:
        testing_session.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, first_seen, last_seen, last_scanned)
                VALUES
                    (:server_id, :first_seen, :last_seen, :last_scanned)
                """
            ),
            {
                "server_id": server_id,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "last_scanned": last_scanned,
            },
        )
    testing_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: testing_session

    response = TestClient(test_app).get("/api/registry/seen-gap")
    assert response.status_code == 200, response.text
    payload = response.json()
    bands = payload["bands"]
    assert sum(int(band["count"]) for band in bands) == len(seeded)
    assert next(
        int(band["count"]) for band in bands if band["band"] == "0-1d"
    ) == 2
    print("PASS")