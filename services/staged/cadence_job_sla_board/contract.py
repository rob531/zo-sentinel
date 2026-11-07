from __future__ import annotations

import sys
from datetime import datetime, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, CadenceJobRun

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    started_at = datetime.now(timezone.utc)
    finished_at = started_at

    session = testing_session()
    try:
        session.add_all(
            [
                CadenceJobRun(
                    id=1,
                    job="scoring_wave",
                    status="success",
                    started_at=started_at,
                    finished_at=finished_at,
                    rows_affected=10,
                    detail={},
                ),
                CadenceJobRun(
                    id=2,
                    job="scoring_wave",
                    status="success",
                    started_at=started_at,
                    finished_at=finished_at,
                    rows_affected=5,
                    detail={},
                ),
                CadenceJobRun(
                    id=3,
                    job="scoring_wave",
                    status="failure",
                    started_at=started_at,
                    finished_at=finished_at,
                    rows_affected=0,
                    detail={},
                ),
                CadenceJobRun(
                    id=4,
                    job="advisory_fetch",
                    status="success",
                    started_at=started_at,
                    finished_at=finished_at,
                    rows_affected=0,
                    detail={},
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/cadence/jobs/sla", params={"days": 7})
    assert response.status_code == 200, response.text

    body = response.json()
    assert len(body["jobs"]) == 2, body

    jobs = {entry["job"]: entry for entry in body["jobs"]}
    scoring = jobs["scoring_wave"]
    assert scoring["runs"] == 3, body
    assert scoring["success_count"] == 2, body
    assert scoring["failure_count"] == 1, body
    assert scoring["success_rate_pct"] == 66.67, body
    assert scoring["total_rows_affected"] == 15, body
    assert len(scoring["daily"]) >= 1, body
    assert body["totals"]["failures"] == 1, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)