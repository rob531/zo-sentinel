"""Acceptance self-test for the axis score delta service."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    try:
        Base.metadata.create_all(bind=engine)
        TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        earlier = datetime(2025, 1, 1, 12, 0, 0)
        later = earlier + timedelta(hours=1)
        values = {
            "1": {"confidentiality": (0.8, 0.6), "integrity": (0.4, 0.5)},
            "2": {"confidentiality": (0.5, 0.5), "integrity": (0.3, 0.3)},
            "3": {"confidentiality": (0.2, 0.4), "integrity": (0.7, 0.6)},
        }

        with TestingSession() as db:
            score_id = 1
            for server_id, axes in values.items():
                for axis_name, (previous_score, current_score) in axes.items():
                    for event_index, (scored_at, p_top) in enumerate(
                        ((earlier, previous_score), (later, current_score))
                    ):
                        db.add(
                            McpLlmAxisScore(
                                id=score_id,
                                server_id=server_id,
                                axis_name=axis_name,
                                model_version=f"contract-{event_index}",
                                scored_at=scored_at,
                                p_top=p_top,
                            )
                        )
                        score_id += 1
            db.commit()

        def override_session():
            db = TestingSession()
            try:
                yield db
            finally:
                db.close()

        test_app = FastAPI()
        test_app.include_router(router)
        test_app.dependency_overrides[get_session] = override_session

        with TestClient(test_app) as client:
            response = client.get(
                "/api/scoring/delta",
                params={"server_id": "1"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["server_id"] == 1, body
        assert len(body["axes"]) == 2, body
        degraded = next(
            (
                axis
                for axis in body["axes"]
                if axis["axis_name"] == "confidentiality"
            ),
            None,
        )
        assert degraded is not None, body
        assert degraded["delta"] < 0, body
        return True
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,), file=sys.stderr)
        sys.exit(1)
    print("PASS")
    sys.exit(0)