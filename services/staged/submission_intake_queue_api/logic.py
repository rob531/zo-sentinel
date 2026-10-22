from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _empty_queue() -> dict[str, Any]:
    return {
        "pending_match": [],
        "needs_score": [],
        "matched": [],
        "counts": {
            "pending_match": 0,
            "needs_score": 0,
            "matched": 0,
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_intake_queue(session: Session = Depends(get_session)) -> dict[str, Any]:
    submissions = session.execute(
        text("SELECT * FROM mcp_submissions")
    ).mappings().all()
    servers = session.execute(select(McpServerRegistry)).scalars().all()

    registry: dict[Any, McpServerRegistry] = {}
    for server in servers:
        registry.setdefault(server.server_id, server)

    result = _empty_queue()

    for submission in submissions:
        row = dict(submission)
        mcp_name = row.get("mcp_name")
        server = registry.get(mcp_name)

        if server is None:
            status = "PENDING_MATCH"
            bucket = "pending_match"
        elif server.last_assessed is None:
            status = "NEEDS_SCORE"
            bucket = "needs_score"
        else:
            status = "MATCHED"
            bucket = "matched"

        row["status"] = status
        row["server_id"] = server.server_id if server is not None else None
        row["last_assessed"] = server.last_assessed if server is not None else None
        result[bucket].append(row)

    result["counts"] = {
        "pending_match": len(result["pending_match"]),
        "needs_score": len(result["needs_score"]),
        "matched": len(result["matched"]),
    }
    return result


def get_submission_intake_queue(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_intake_queue(session=session)


if __name__ == "__main__":
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_submissions (
                    id INTEGER PRIMARY KEY,
                    mcp_name TEXT,
                    requested_by TEXT,
                    submitted_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_submissions
                    (mcp_name, requested_by, submitted_at)
                VALUES
                    ('scored-server', 'alice', '2026-01-01T00:00:00Z'),
                    ('unscored-server', 'bob', '2026-01-02T00:00:00Z'),
                    ('missing-server-one', 'carol', '2026-01-03T00:00:00Z'),
                    ('missing-server-two', 'dave', '2026-01-04T00:00:00Z')
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="scored-server",
                    name="Scored Server",
                    last_assessed=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="unscored-server",
                    name="Unscored Server",
                    last_assessed=None,
                ),
                McpServerRegistry(
                    server_id="unused-server",
                    name="Unused Server",
                    last_assessed=datetime(2026, 1, 1, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/submissions/intake-queue")
    def intake_queue(session: Session = Depends(get_session)):
        return get_intake_queue(session=session)

    with TestClient(that_app) as client:
        response = client.get("/api/submissions/intake-queue")
        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"]["pending_match"] == 2
        assert payload["counts"]["needs_score"] == 1
        assert payload["counts"]["matched"] == 1

    print("PASS")