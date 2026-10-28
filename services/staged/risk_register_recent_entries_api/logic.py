from typing import Any, Dict, List, Optional

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RiskRegisterEntryResponse(BaseModel):
    server_id: Optional[str] = None
    computed_at: Optional[str] = None
    overall_risk: Optional[float] = None
    risk_tier: Optional[str] = None
    verdict: Optional[str] = None
    detail: Optional[Any] = None


class RiskRegisterEntriesResponse(BaseModel):
    total: int
    limit: int
    offset: int
    entries: List[RiskRegisterEntryResponse]


def list_recent_entries(
    db: Session,
    server_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Return recent risk-register entries and the matching total."""
    limit = min(max(int(limit), 1), 200)
    offset = max(int(offset), 0)

    if server_id is None:
        count_sql = "SELECT COUNT(*) AS total FROM mcp_risk_register"
        entries_sql = """
            SELECT
                server_id,
                computed_at,
                overall_risk,
                risk_tier,
                verdict,
                detail
            FROM mcp_risk_register
            ORDER BY computed_at DESC
            LIMIT :limit OFFSET :offset
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
    else:
        count_sql = """
            SELECT COUNT(*) AS total
            FROM mcp_risk_register
            WHERE server_id = :server_id
        """
        entries_sql = """
            SELECT
                server_id,
                computed_at,
                overall_risk,
                risk_tier,
                verdict,
                detail
            FROM mcp_risk_register
            WHERE server_id = :server_id
            ORDER BY computed_at DESC
            LIMIT :limit OFFSET :offset
        """
        params = {
            "server_id": server_id,
            "limit": limit,
            "offset": offset,
        }

    total = int(db.execute(text(count_sql), params).scalar_one())
    rows = db.execute(text(entries_sql), params).mappings().all()

    entries: List[Dict[str, Any]] = []
    for row in rows:
        entries.append(
            {
                "server_id": row["server_id"],
                "computed_at": row["computed_at"],
                "overall_risk": row["overall_risk"],
                "risk_tier": row["risk_tier"],
                "verdict": row["verdict"],
                "detail": row["detail"],
            }
        )

    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "entries": entries,
    }


list_risk_register_entries = list_recent_entries
get_risk_register_entries = list_recent_entries
get_recent_entries = list_recent_entries


if __name__ == "__main__":
    from datetime import datetime, timedelta

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    computed_at TIMESTAMP NOT NULL,
                    overall_risk REAL,
                    risk_tier TEXT,
                    verdict TEXT,
                    detail TEXT
                )
                """
            )
        )

        base_time = datetime(2025, 1, 1, 12, 0, 0)
        rows = [
            (
                "server-alpha",
                base_time + timedelta(hours=4),
                82.0,
                "high",
                "review",
                "fourth alpha change",
            ),
            (
                "server-beta",
                base_time + timedelta(hours=3),
                31.0,
                "low",
                "accept",
                "third beta change",
            ),
            (
                "server-alpha",
                base_time + timedelta(hours=2),
                67.0,
                "medium",
                "review",
                "third alpha change",
            ),
            (
                "server-beta",
                base_time + timedelta(hours=1),
                44.0,
                "medium",
                "monitor",
                "second beta change",
            ),
            (
                "server-alpha",
                base_time,
                20.0,
                "low",
                "accept",
                "first alpha change",
            ),
        ]
        connection.execute(
            text(
                """
                INSERT INTO mcp_risk_register (
                    server_id,
                    computed_at,
                    overall_risk,
                    risk_tier,
                    verdict,
                    detail
                ) VALUES (
                    :server_id,
                    :computed_at,
                    :overall_risk,
                    :risk_tier,
                    :verdict,
                    :detail
                )
                """
            ),
            [
                {
                    "server_id": server_id,
                    "computed_at": computed_at,
                    "overall_risk": overall_risk,
                    "risk_tier": risk_tier,
                    "verdict": verdict,
                    "detail": detail,
                }
                for server_id, computed_at, overall_risk, risk_tier, verdict, detail in rows
            ],
        )

    def override_get_session():
        with test_engine.connect() as connection:
            transaction = connection.begin()
            try:
                yield connection
                transaction.commit()
            except Exception:
                transaction.rollback()
                raise

    test_app = FastAPI()

    @test_app.get("/api/risk-register/entries")
    def risk_register_entries_endpoint(
        server_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        db: Session = Depends(get_session),
    ):
        return list_recent_entries(
            db,
            server_id=server_id,
            limit=limit,
            offset=offset,
        )

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/risk-register/entries")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 5
        assert len(payload["entries"]) == 5

        computed_times = [entry["computed_at"] for entry in payload["entries"]]
        parsed_times = [datetime.fromisoformat(value) for value in computed_times]
        assert parsed_times == sorted(parsed_times, reverse=True)

        filtered = client.get(
            "/api/risk-register/entries",
            params={"server_id": "server-alpha"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["total"] == 3
        assert len(filtered_payload["entries"]) == 3
        assert all(
            entry["server_id"] == "server-alpha"
            for entry in filtered_payload["entries"]
        )

    print("PASS")