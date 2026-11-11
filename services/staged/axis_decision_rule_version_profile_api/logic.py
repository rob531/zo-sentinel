from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Iterator, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


__all__ = ["get_decision_rule_version_profile"]


@contextmanager
def _session_scope() -> Iterator[Session]:
    session = get_session()
    if hasattr(session, "__enter__") and hasattr(session, "__exit__"):
        with session as active_session:
            yield active_session
        return
    try:
        yield session
    finally:
        close = getattr(session, "close", None)
        if close is not None:
            close()


def get_decision_rule_version_profile(
    days: int = 30,
    db: Optional[Session] = None,
    session: Optional[Session] = None,
) -> dict:
    """Return the distribution of decision-rule versions in the score store."""
    active_session = db if db is not None else session
    if active_session is None:
        with _session_scope() as owned_session:
            return _build_profile(owned_session, days)
    return _build_profile(active_session, days)


def _build_profile(db: Session, days: int) -> dict:
    cutoff = datetime.utcnow() - timedelta(days=days)
    result = db.execute(
        text(
            """
            SELECT
                decision_rule_version,
                COUNT(*) AS row_count,
                COUNT(DISTINCT axis_name) AS axis_count,
                COUNT(DISTINCT server_id) AS server_count,
                MIN(scored_at) AS first_scored_at,
                MAX(scored_at) AS last_scored_at
            FROM mcp_llm_axis_scores
            WHERE scored_at >= :cutoff
            GROUP BY decision_rule_version
            ORDER BY decision_rule_version
            """
        ),
        {"cutoff": cutoff},
    ).mappings()

    versions = []
    total_rows = 0
    for row in result:
        row_count = int(row["row_count"])
        total_rows += row_count
        versions.append(
            {
                "decision_rule_version": str(row["decision_rule_version"] or ""),
                "rows": row_count,
                "axis_count": int(row["axis_count"] or 0),
                "server_count": int(row["server_count"] or 0),
                "first_scored_at": _timestamp_string(row["first_scored_at"]),
                "last_scored_at": _timestamp_string(row["last_scored_at"]),
            }
        )

    return {
        "days": days,
        "versions": versions,
        "total_rows": total_rows,
    }


def _timestamp_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session = sessionmaker(bind=engine)()

    test_session.execute(
        text(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                axis_name VARCHAR NOT NULL,
                decision_rule_version VARCHAR NOT NULL,
                model_version VARCHAR,
                scored_at DATETIME NOT NULL,
                server_id VARCHAR NOT NULL
            )
            """
        )
    )

    now = datetime.utcnow()
    rows = []
    row_id = 1
    for server_id in ("server-1", "server-2", "server-3"):
        for version in ("v1", "v2", "v3"):
            for axis in ("axis-a", "axis-b"):
                rows.append(
                    {
                        "id": row_id,
                        "axis_name": axis,
                        "decision_rule_version": version,
                        "model_version": "model-1",
                        "scored_at": now,
                        "server_id": server_id,
                    }
                )
                row_id += 1

    test_session.execute(
        text(
            """
            INSERT INTO mcp_llm_axis_scores
                (id, axis_name, decision_rule_version, model_version, scored_at, server_id)
            VALUES
                (:id, :axis_name, :decision_rule_version, :model_version, :scored_at, :server_id)
            """
        ),
        rows,
    )
    test_session.commit()

    that_app = FastAPI()

    @that_app.get("/api/axes/decision-rule-versions")
    def decision_rule_version_profile(
        days: int = 30,
        db: Session = Depends(get_session),
    ) -> dict:
        return get_decision_rule_version_profile(days=days, db=db)

    that_app.dependency_overrides[get_session] = lambda: test_session
    response = TestClient(that_app).get("/api/axes/decision-rule-versions")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["versions"]) == 3
    assert next(
        version
        for version in payload["versions"]
        if version["decision_rule_version"] == "v1"
    )["rows"] == 6
    print("PASS")