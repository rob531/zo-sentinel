from typing import Any, Dict, Iterator, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import MetaData, Table, literal, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class ExemptionItem(BaseModel):
    server_id: str
    reason: Optional[str] = None
    status: Optional[str] = None


class PolicyRuleItem(BaseModel):
    rule_type: str
    pattern: str


class ExemptionPolicyInventoryResponse(BaseModel):
    exemptions: List[ExemptionItem]
    policy_rules: List[PolicyRuleItem]
    counts: Dict[str, int]


def _find_column(table: Table, names: tuple) -> Any:
    for name in names:
        if name in table.c:
            return table.c[name]
    return None


def _reflect_table(session: Session, name: str) -> Table:
    metadata = MetaData()
    return Table(name, metadata, autoload_with=session.get_bind())


def _inventory(session: Session, status: Optional[str]) -> ExemptionPolicyInventoryResponse:
    exemptions_table = _reflect_table(session, "mcp_exemptions")
    policy_rules_table = _reflect_table(session, "mcp_policy_rules")

    server_id_column = _find_column(exemptions_table, ("server_id",))
    reason_column = _find_column(
        exemptions_table, ("reason", "exemption_reason", "justification")
    )
    status_column = _find_column(
        exemptions_table, ("status", "exemption_status", "state")
    )

    if server_id_column is None:
        raise RuntimeError("mcp_exemptions.server_id is required")

    selected_exemptions = [
        server_id_column.label("server_id"),
        (
            reason_column.label("reason")
            if reason_column is not None
            else literal(None).label("reason")
        ),
        (
            status_column.label("status")
            if status_column is not None
            else literal(None).label("status")
        ),
    ]
    exemption_query = select(*selected_exemptions)
    if status is not None and status_column is not None:
        exemption_query = exemption_query.where(status_column == status)

    exemption_rows = session.execute(exemption_query).mappings().all()

    rule_type_column = _find_column(policy_rules_table, ("rule_type",))
    pattern_column = _find_column(policy_rules_table, ("pattern",))
    if rule_type_column is None or pattern_column is None:
        raise RuntimeError(
            "mcp_policy_rules.rule_type and mcp_policy_rules.pattern are required"
        )

    policy_query = select(
        rule_type_column.label("rule_type"),
        pattern_column.label("pattern"),
    )
    policy_rows = session.execute(policy_query).mappings().all()

    exemptions = [
        {
            "server_id": row["server_id"],
            "reason": row["reason"],
            "status": row["status"],
        }
        for row in exemption_rows
    ]
    policy_rules = [
        {
            "rule_type": row["rule_type"],
            "pattern": row["pattern"],
        }
        for row in policy_rows
    ]

    return ExemptionPolicyInventoryResponse(
        exemptions=exemptions,
        policy_rules=policy_rules,
        counts={
            "exemptions": len(exemptions),
            "policy_rules": len(policy_rules),
        },
    )


@router.get(
    "/admin/exemption-policy-inventory",
    response_model=ExemptionPolicyInventoryResponse,
)
def get_exemption_policy_inventory(
    status: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
) -> ExemptionPolicyInventoryResponse:
    return _inventory(session, status)


def run_contract() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, Integer, MetaData, String, Table, Text, create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    Table(
        "mcp_exemptions",
        metadata,
        Column("server_id", String, primary_key=True),
        Column("reason", Text),
        Column("status", String, nullable=False),
        Column("expires_at", String),
    )
    Table(
        "mcp_policy_rules",
        metadata,
        Column("id", Integer, primary_key=True),
        Column("rule_type", String, nullable=False),
        Column("pattern", Text, nullable=False),
        Column("description", Text),
    )
    metadata.create_all(engine)

    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with testing_session() as session:
        session.execute(
            insert(metadata.tables["mcp_exemptions"]).values(
                server_id="seeded-server",
                reason="Approved exception",
                status="active",
                expires_at=None,
            )
        )
        session.execute(
            insert(metadata.tables["mcp_policy_rules"]).values(
                id=1,
                rule_type="allow",
                pattern="example.com/*",
                description="Seeded allow rule",
            )
        )
        session.execute(
            insert(metadata.tables["mcp_policy_rules"]).values(
                id=2,
                rule_type="deny",
                pattern="blocked.example/*",
                description="Seeded deny rule",
            )
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with testing_session() as session:
            yield session

    local_app = FastAPI()
    local_app.include_router(router)
    local_app.dependency_overrides[get_session] = override_get_session

    with TestClient(local_app) as client:
        response = client.get("/api/admin/exemption-policy-inventory")
        assert response.status_code == 200
        payload = response.json()
        assert payload["counts"]["exemptions"] == 1
        assert payload["counts"]["policy_rules"] == 2
        assert payload["exemptions"][0]["server_id"] == "seeded-server"

        filtered = client.get(
            "/api/admin/exemption-policy-inventory",
            params={"status": "active"},
        )
        assert filtered.status_code == 200
        assert filtered.json()["counts"]["exemptions"] == 1

    print("PASS")


if __name__ == "__main__":
    run_contract()