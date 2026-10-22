from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User  # noqa: F401
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class AuditTrailEntry(BaseModel):
    id: int
    timestamp: datetime
    actor: Optional[str]
    action: str
    target_server_id: Optional[str]
    detail: Any


class AuditTrailResponse(BaseModel):
    count: int
    limit: int
    offset: int
    entries: List[AuditTrailEntry]


router = APIRouter(prefix="/api")


def _escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _audit_query(
    server_id: Optional[str],
    target: Optional[str],
    since_days: int,
) -> tuple[str, Dict[str, Any]]:
    clauses = ["timestamp >= :since"]
    params: Dict[str, Any] = {
        "since": datetime.now(timezone.utc) - timedelta(days=since_days)
    }

    if server_id is not None:
        clauses.append("target_server_id = :server_id")
        params["server_id"] = server_id

    if target is not None:
        pattern = "%" + _escape_like(target) + "%"
        clauses.append(
            "(LOWER(action) LIKE :target ESCAPE '!' "
            "OR LOWER(actor) LIKE :target ESCAPE '!')"
        )
        params["target"] = pattern

    return " AND ".join(clauses), params


@router.get("/audit/trail", response_model=AuditTrailResponse)
def get_audit_trail(
    server_id: Optional[str] = Query(default=None),
    target: Optional[str] = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    since_days: int = Query(default=30),
    session: Session = Depends(get_session),
) -> AuditTrailResponse:
    limit = max(1, min(200, limit))
    offset = max(0, offset)
    since_days = max(1, min(365, since_days))

    where_sql, params = _audit_query(server_id, target, since_days)
    params.update({"limit": limit, "offset": offset})

    count_sql = text(
        "SELECT COUNT(*) AS count FROM audit_log WHERE " + where_sql
    )
    entries_sql = text(
        "SELECT id, timestamp, actor, action, target_server_id, detail "
        "FROM audit_log WHERE "
        + where_sql
        + " ORDER BY timestamp DESC, id DESC LIMIT :limit OFFSET :offset"
    )

    try:
        count_row = session.execute(count_sql, params).mappings().one()
        rows = session.execute(entries_sql, params).mappings().all()
        entries = [AuditTrailEntry(**dict(row)) for row in rows]
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "audit_log unavailable"},
        )

    return AuditTrailResponse(
        count=int(count_row["count"]),
        limit=limit,
        offset=offset,
        entries=entries,
    )


app = FastAPI()
app.include_router(router)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE audit_log ("
                "id INTEGER PRIMARY KEY, "
                "timestamp TIMESTAMP NOT NULL, "
                "actor TEXT, "
                "action TEXT NOT NULL, "
                "target_server_id TEXT, "
                "detail TEXT"
                ")"
            )
        )

        now = datetime.now(timezone.utc)
        rows = [
            (1, now - timedelta(days=1), "admin", "reviewed", "server-a", "first"),
            (2, now - timedelta(days=1), "analyst", "exported", "server-b", "second"),
            (3, now - timedelta(days=2), "admin", "updated", "server-a", "third"),
            (4, now - timedelta(days=2), "operator", "acknowledged", "server-b", "fourth"),
            (5, now - timedelta(days=3), "analyst", "inspected", "server-a", "fifth"),
            (6, now - timedelta(days=3), "admin", "commented", "server-b", "sixth"),
        ]
        connection.execute(
            text(
                "INSERT INTO audit_log "
                "(id, timestamp, actor, action, target_server_id, detail) "
                "VALUES (:id, :timestamp, :actor, :action, "
                ":target_server_id, :detail)"
            ),
            [
                {
                    "id": row[0],
                    "timestamp": row[1],
                    "actor": row[2],
                    "action": row[3],
                    "target_server_id": row[4],
                    "detail": row[5],
                }
                for row in rows
            ],
        )

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/audit/trail")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["entries"]
        assert payload["count"] == 6
        for entry in payload["entries"]:
            assert {"id", "timestamp", "action", "target_server_id"} <= entry.keys()

        limited = client.get("/api/audit/trail", params={"limit": 2})
        assert limited.status_code == 200, limited.text
        assert limited.json()["limit"] == 2
        assert len(limited.json()["entries"]) == 2

        filtered = client.get(
            "/api/audit/trail",
            params={"server_id": "server-a"},
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["entries"]
        assert all(
            entry["target_server_id"] == "server-a"
            for entry in filtered.json()["entries"]
        )

        bad_limit = client.get("/api/audit/trail", params={"limit": 0})
        assert bad_limit.status_code != 500
        assert bad_limit.status_code == 200
        assert bad_limit.json()["limit"] == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()