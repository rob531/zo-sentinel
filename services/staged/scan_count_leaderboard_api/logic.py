from typing import Any, Dict, List, Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class ScanCountLeaderboard(list):
    """Leaderboard rows with the total number of matching servers."""

    def __init__(self, rows: List[Dict[str, Any]], total: int):
        super().__init__(rows)
        self.total = total
        self.rows = rows

    def __getitem__(self, key: Any) -> Any:
        if key == "total":
            return self.total
        if key == "rows":
            return self.rows
        return super().__getitem__(key)


def get_scan_count_leaderboard(
    session: Session,
    limit: int = 50,
    risk_tier: Optional[str] = None,
) -> ScanCountLeaderboard:
    """Return servers ordered by scan count, newest qualifying scan first."""
    normalized_limit = max(1, min(int(limit), 200))

    filters = []
    if risk_tier:
        filters.append(McpServerRegistry.risk_tier == risk_tier)

    total_stmt = select(func.count()).select_from(McpServerRegistry)
    rows_stmt = select(
        McpServerRegistry.server_id,
        McpServerRegistry.name,
        McpServerRegistry.risk_tier,
        McpServerRegistry.scan_count,
        McpServerRegistry.last_seen,
        McpServerRegistry.last_scanned,
    )

    if filters:
        total_stmt = total_stmt.where(*filters)
        rows_stmt = rows_stmt.where(*filters)

    total = int(session.execute(total_stmt).scalar_one())

    rows_stmt = rows_stmt.order_by(
        case((McpServerRegistry.scan_count.is_(None), 1), else_=0),
        McpServerRegistry.scan_count.desc(),
        McpServerRegistry.server_id.asc(),
    ).limit(normalized_limit)

    records = session.execute(rows_stmt).mappings().all()
    rows = [
        {
            "server_id": record["server_id"],
            "name": record["name"],
            "risk_tier": record["risk_tier"],
            "scan_count": record["scan_count"],
            "last_seen": record["last_seen"],
        }
        for record in records
    ]

    return ScanCountLeaderboard(rows=rows, total=total)


def _self_test() -> None:
    from fastapi import Depends, FastAPI, Query
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    risk_tier VARCHAR NOT NULL,
                    scan_count INTEGER NOT NULL,
                    last_seen VARCHAR,
                    last_scanned VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier, scan_count, last_seen, last_scanned)
                VALUES
                    ('server-1', 'Alpha', 'high', 5, '2025-01-03T00:00:00', '2025-01-03T00:00:00'),
                    ('server-2', 'Beta', 'medium', 2, '2025-01-02T00:00:00', '2025-01-02T00:00:00'),
                    ('server-3', 'Gamma', 'low', 0, '2025-01-01T00:00:00', '2025-01-01T00:00:00')
                """
            )
        )

    test_app = FastAPI()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/servers/scan-counts")
    def scan_counts(
        limit: int = Query(50, ge=1, le=200),
        risk_tier: Optional[str] = Query(None),
        session: Session = Depends(get_session),
    ):
        leaderboard = get_scan_count_leaderboard(
            session=session,
            limit=limit,
            risk_tier=risk_tier,
        )
        return {"total": leaderboard.total, "rows": leaderboard.rows}

    with TestClient(test_app) as client:
        response = client.get("/api/servers/scan-counts")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["rows"]) == 3
        assert payload["rows"][0]["scan_count"] == 5

        filtered = client.get(
            "/api/servers/scan-counts",
            params={"risk_tier": "high"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert len(filtered_payload["rows"]) == 1
        assert filtered_payload["total"] == 1
        assert filtered_payload["rows"][0]["server_id"] == "server-1"

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()