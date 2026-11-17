from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_count_leaderboard

router = APIRouter(prefix="/api")


class ScanCountLeaderboardRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    server_id: str
    name: str
    risk_tier: str | None = None
    scan_count: int
    last_seen: datetime | None = None


class ScanCountLeaderboardResponse(BaseModel):
    total: int
    rows: list[ScanCountLeaderboardRow]


@router.get(
    "/servers/scan-counts",
    response_model=ScanCountLeaderboardResponse,
)
def scan_count_leaderboard(
    limit: int = Query(default=50, ge=1, le=200),
    risk_tier: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    result = get_scan_count_leaderboard(
        session,
        limit=limit,
        risk_tier=risk_tier,
    )
    if isinstance(result, dict):
        return result
    if isinstance(result, tuple) and len(result) == 2:
        total, rows = result
        return {"total": total, "rows": rows}
    return {"total": len(result), "rows": result}


if __name__ == "__main__":
    from datetime import timezone

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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    seed_session = TestSession()
    seed_session.add_all(
        [
            McpServerRegistry(
                server_id="server-high",
                name="High Scan Server",
                risk_tier="high",
                scan_count=5,
                last_seen=datetime.now(timezone.utc),
            ),
            McpServerRegistry(
                server_id="server-medium",
                name="Medium Scan Server",
                risk_tier="medium",
                scan_count=2,
                last_seen=datetime.now(timezone.utc),
            ),
            McpServerRegistry(
                server_id="server-low",
                name="Low Scan Server",
                risk_tier="low",
                scan_count=0,
                last_seen=datetime.now(timezone.utc),
            ),
        ]
    )
    seed_session.commit()
    seed_session.close()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/servers/scan-counts")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 3
        assert len(payload["rows"]) == 3
        assert payload["rows"][0]["scan_count"] == 5

        filtered = client.get(
            "/api/servers/scan-counts",
            params={"risk_tier": "high"},
        )
        assert filtered.status_code == 200
        filtered_payload = filtered.json()
        assert filtered_payload["total"] == 1
        assert len(filtered_payload["rows"]) == 1
        assert filtered_payload["rows"][0]["risk_tier"] == "high"

    print("PASS")