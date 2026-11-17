from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry


_REGISTRY_MODEL = McpServerRegistry

router = APIRouter(tags=["admin", "exemption-policy-inventory"])


class ExemptionInventoryItem(BaseModel):
    server_id: str
    reason: str
    status: str


class PolicyRuleInventoryItem(BaseModel):
    rule_type: str
    pattern: str


class ExemptionPolicyInventoryResponse(BaseModel):
    exemptions: List[ExemptionInventoryItem]
    policy_rules: List[PolicyRuleInventoryItem]
    counts: Dict[str, int]


def get_exemption_policy_inventory(
    session: Any,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    exemption_sql = text(
        """
        SELECT server_id, reason, status
        FROM mcp_exemptions
        """
    )
    exemption_params: Dict[str, Any] = {}

    if status is not None:
        exemption_sql = text(
            """
            SELECT server_id, reason, status
            FROM mcp_exemptions
            WHERE status = :status
            """
        )
        exemption_params["status"] = status

    exemption_rows = session.execute(
        exemption_sql,
        exemption_params,
    ).mappings().all()

    policy_rows = session.execute(
        text(
            """
            SELECT rule_type, pattern
            FROM mcp_policy_rules
            """
        )
    ).mappings().all()

    exemptions = [
        {
            "server_id": str(row["server_id"]),
            "reason": "" if row["reason"] is None else str(row["reason"]),
            "status": str(row["status"]),
        }
        for row in exemption_rows
    ]
    policy_rules = [
        {
            "rule_type": str(row["rule_type"]),
            "pattern": "" if row["pattern"] is None else str(row["pattern"]),
        }
        for row in policy_rows
    ]

    return {
        "exemptions": exemptions,
        "policy_rules": policy_rules,
        "counts": {
            "exemptions": len(exemptions),
            "policy_rules": len(policy_rules),
        },
    }


def get_inventory(
    session: Any,
    status: Optional[str] = None,
) -> Dict[str, Any]:
    return get_exemption_policy_inventory(session, status)


@router.get(
    "/admin/exemption-policy-inventory",
    response_model=ExemptionPolicyInventoryResponse,
)
def exemption_policy_inventory(
    status: Optional[str] = Query(default=None),
    session: Any = Depends(get_session),
) -> Dict[str, Any]:
    return get_exemption_policy_inventory(session, status)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_exemptions (
                    server_id VARCHAR PRIMARY KEY,
                    reason TEXT,
                    status VARCHAR NOT NULL,
                    expires_at TIMESTAMP NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_policy_rules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_type VARCHAR NOT NULL,
                    pattern TEXT NOT NULL,
                    description TEXT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_exemptions
                    (server_id, reason, status, expires_at)
                VALUES
                    (:server_id, :reason, :status, NULL)
                """
            ),
            {
                "server_id": "seeded-server",
                "reason": "acceptance fixture",
                "status": "active",
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_policy_rules
                    (rule_type, pattern, description)
                VALUES
                    ('allow', 'example.test/*', 'first rule'),
                    ('deny', 'blocked.test/*', 'second rule')
                """
            )
        )

    test_session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/admin/exemption-policy-inventory")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["counts"]["exemptions"] == 1, payload
        assert payload["counts"]["policy_rules"] == 2, payload
        assert any(
            item["server_id"] == "seeded-server"
            for item in payload["exemptions"]
        ), payload

        filtered = client.get(
            "/api/admin/exemption-policy-inventory",
            params={"status": "active"},
        )
        assert filtered.status_code == 200, filtered.text
        assert filtered.json()["counts"]["exemptions"] == 1, filtered.text

    print("PASS")


if __name__ == "__main__":
    _self_test()