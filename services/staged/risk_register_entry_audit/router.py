"""HTTP surface for the risk register entry audit service."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import list_risk_register_entries

router = APIRouter(prefix="/api", tags=["risk-register-entry-audit"])


class RiskRegisterEntry(BaseModel):
    server_id: str
    computed_at: datetime
    risk_value: float
    verdict: str
    source: str


class RiskRegisterEntriesResponse(BaseModel):
    count: int
    entries: list[RiskRegisterEntry]


@router.get(
    "/risk-register/entries",
    response_model=RiskRegisterEntriesResponse,
)
def get_risk_register_entries(
    limit: int = Query(default=100),
    server_id: str | None = Query(default=None),
    db: Session = Depends(get_session),
) -> RiskRegisterEntriesResponse:
    bounded_limit = max(1, min(500, limit))
    result = list_risk_register_entries(db, bounded_limit, server_id)

    if isinstance(result, dict):
        raw_entries = result.get("entries", [])
    else:
        raw_entries = result

    entries = [RiskRegisterEntry.model_validate(entry) for entry in raw_entries]
    return RiskRegisterEntriesResponse(count=len(entries), entries=entries)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[get_session] = lambda: None

    def _audit_entries(
        _db: Session,
        limit: int,
        server_id: str | None,
    ) -> list[dict[str, Any]]:
        rows = [
            {
                "server_id": "server-a",
                "computed_at": "2024-01-03T00:00:00Z",
                "risk_value": 3.0,
                "verdict": "review",
                "source": "pipeline",
            },
            {
                "server_id": "server-b",
                "computed_at": "2024-01-02T00:00:00Z",
                "risk_value": 2.0,
                "verdict": "allow",
                "source": "pipeline",
            },
            {
                "server_id": "server-c",
                "computed_at": "2024-01-01T00:00:00Z",
                "risk_value": 1.0,
                "verdict": "allow",
                "source": "pipeline",
            },
        ]
        if server_id is not None:
            rows = [row for row in rows if row["server_id"] == server_id]
        return rows[:limit]

    import __main__ as self_module

    self_module.list_risk_register_entries = _audit_entries
    client = TestClient(application)
    response = client.get("/api/risk-register/entries")
    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert [
        entry["computed_at"]
        for entry in response.json()["entries"]
    ] == [
        "2024-01-03T00:00:00Z",
        "2024-01-02T00:00:00Z",
        "2024-01-01T00:00:00Z",
    ]
    filtered = client.get(
        "/api/risk-register/entries",
        params={"server_id": "server-b"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["count"] == 1
    print("PASS")