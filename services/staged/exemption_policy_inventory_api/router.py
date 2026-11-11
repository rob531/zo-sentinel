from __future__ import annotations

from inspect import isawaitable
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry
from .logic import get_exemption_policy_inventory

router = APIRouter()


class ExemptionInventoryItem(BaseModel):
    server_id: str
    reason: str | None = None
    status: str


class PolicyRuleInventoryItem(BaseModel):
    rule_type: str
    pattern: str


class ExemptionPolicyInventoryCounts(BaseModel):
    exemptions: int
    policy_rules: int


class ExemptionPolicyInventoryResponse(BaseModel):
    exemptions: list[ExemptionInventoryItem]
    policy_rules: list[PolicyRuleInventoryItem]
    counts: ExemptionPolicyInventoryCounts


@router.get(
    "/admin/exemption-policy-inventory",
    response_model=ExemptionPolicyInventoryResponse,
)
async def exemption_policy_inventory(
    status: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> Any:
    result = get_exemption_policy_inventory(session=session, status=status)
    if isawaitable(result):
        result = await result
    return result


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_exemptions (
                    server_id TEXT PRIMARY KEY,
                    reason TEXT,
                    status TEXT NOT NULL,
                    expires_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_policy_rules (
                    id INTEGER PRIMARY KEY,
                    rule_type TEXT NOT NULL,
                    pattern TEXT NOT NULL,
                    description TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_exemptions (server_id, reason, status, expires_at)
                VALUES (:server_id, :reason, :status, NULL)
                """
            ),
            {
                "server_id": "seeded-server",
                "reason": "Approved exemption",
                "status": "active",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_policy_rules (id, rule_type, pattern, description)
                VALUES (:id, :rule_type, :pattern, :description)
                """
            ),
            [
                {
                    "id": 1,
                    "rule_type": "allow",
                    "pattern": "example.com/*",
                    "description": "Allow example.com",
                },
                {
                    "id": 2,
                    "rule_type": "deny",
                    "pattern": "blocked.example/*",
                    "description": "Deny blocked.example",
                },
            ],
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/admin/exemption-policy-inventory")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["counts"]["exemptions"] == 1, payload
    assert payload["counts"]["policy_rules"] == 2, payload
    assert payload["exemptions"][0]["server_id"] == "seeded-server", payload
    assert len(payload["policy_rules"]) == 2, payload

    print("PASS")