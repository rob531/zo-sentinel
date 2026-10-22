from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import DateTime, bindparam, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import User


router = APIRouter(prefix="/api", tags=["audit"])


class AuditEntry(BaseModel):
    id: int
    timestamp: datetime | None
    actor: str | None
    action: str | None
    target_server_id: str | None
    detail: Any | None


class AuditTrailResponse(BaseModel):
    count: int
    limit: int
    offset: int
    entries: list[AuditEntry]


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


@router.get("/audit/trail", response_model=AuditTrailResponse)
def get_audit_trail(
    server_id: str | None = Query(default=None),
    target: str | None = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    since_days: int = Query(default=30),
    db: Session = Depends(get_session),
) -> AuditTrailResponse | JSONResponse:
    limit = _clamp(limit, 1, 200)
    offset = max(0, offset)
    since_days = _clamp(since_days, 1, 365)

    clauses: list[str] = []
    params: dict[str, Any] = {
        "since": bindparam(
            "since",
            datetime.now(timezone.utc) - timedelta(days=since_days),
            type_=DateTime(timezone=True),
        )
    }

    if server_id is not None:
        clauses.append("target_server_id = :server_id")
        params["server_id"] = server_id

    if target is not None:
        clauses.append(
            "(LOWER(CAST(actor AS TEXT)) LIKE :target "
            "OR LOWER(CAST(action AS TEXT)) LIKE :target)"
        )
        params["target"] = f"%{target.lower()}%"

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    count_sql = text(f"SELECT COUNT(*) AS count FROM audit_log{where}")
    trail_sql = text(
        "SELECT id, timestamp, actor, action, target_server_id, detail "
        f"FROM audit_log{where} "
        "ORDER BY timestamp DESC, id DESC "
        "LIMIT :limit OFFSET :offset"
    )
    query_params = dict(params)
    query_params["limit"] = limit
    query_params["offset"] = offset

    try:
        count = int(db.execute(count_sql, params).scalar_one())
        rows = db.execute(trail_sql, query_params).mappings().all()
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "audit_log unavailable"},
        )

    entries = [AuditEntry(**dict(row)) for row in rows]
    return AuditTrailResponse(
        count=count,
        limit=limit,
        offset=offset,
        entries=entries,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.execute(
            text(
                "CREATE TABLE audit_log ("
                "id INTEGER PRIMARY KEY, "
                "timestamp TEXT NOT NULL, "
                "actor TEXT, "
                "action TEXT, "
                "target_server_id TEXT, "
                "detail TEXT"
                ")"
            )
        )
        now = datetime.now(timezone.utc)
        session.execute(
            text(
                "INSERT INTO audit_log "
                "(id, timestamp, actor, action, target_server_id, detail) "
                "VALUES (:id, :timestamp, :actor, :action, :server_id, :detail)"
            ),
            [
                {
                    "id": 1,
                    "timestamp": (now - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S"),
                    "actor": "alice",
                    "action": "server.registered",
                    "server_id": "server-a",
                    "detail": "Registered server A",
                },
                {
                    "id": 2,
                    "timestamp": (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
                    "actor": "bob",
                    "action": "server.reviewed",
                    "server_id": "server-b",
                    "detail": "Reviewed server B",
                },
                {
                    "id": 3,
                    "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "actor": "alice",
                    "action": "server.updated",
                    "server_id": "server-a",
                    "detail": "Updated server A",
                },
                {
                    "id": 4,
                    "timestamp": (now - timedelta(hours=6)).strftime("%Y-%m-%d %H:%M:%S"),
                    "actor": "carol",
                    "action": "policy.applied",
                    "server_id": "server-b",
                    "detail": "Applied policy",
                },
                {
                    "id": 5,
                    "timestamp": (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
                    "actor": "bob",
                    "action": "server.dismissed",
                    "server_id": "server-a",
                    "detail": "Dismissed server A",
                },
            ],
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/audit/trail")
        assert response.status_code == 200
        payload = response.json()
        assert payload["entries"]
        assert all(
            {"id", "timestamp", "action", "target_server_id"}.issubset(entry)
            for entry in payload["entries"]
        )

        response = client.get("/api/audit/trail", params={"limit": 2})
        assert response.status_code == 200
        assert len(response.json()["entries"]) == 2

        response = client.get(
            "/api/audit/trail",
            params={"server_id": "server-a", "limit": 50},
        )
        assert response.status_code == 200
        assert response.json()["entries"]
        assert all(
            entry["target_server_id"] == "server-a"
            for entry in response.json()["entries"]
        )

        response = client.get("/api/audit/trail", params={"limit": 0})
        assert response.status_code == 200
        assert response.json()["limit"] == 1

    print("PASS")