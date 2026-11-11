from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


router = APIRouter(tags=["scoring"])


class DecisionRuleVersionRollup(BaseModel):
    decision_rule_version: Optional[str]
    row_count: int
    server_count: int
    first_scored_at: Optional[datetime]
    last_scored_at: Optional[datetime]


class DecisionRuleVersionRollupResponse(BaseModel):
    total_rows: int
    versions: List[DecisionRuleVersionRollup]
    generated_at: str


def fetch_decision_rule_versions(
    db: Session,
    days: Optional[int] = None,
) -> DecisionRuleVersionRollupResponse:
    statement = (
        select(
            McpLlmAxisScore.decision_rule_version.label("decision_rule_version"),
            func.count(McpLlmAxisScore.id).label("row_count"),
            func.count(distinct(McpLlmAxisScore.server_id)).label("server_count"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .group_by(McpLlmAxisScore.decision_rule_version)
        .order_by(
            func.count(McpLlmAxisScore.id).desc(),
            McpLlmAxisScore.decision_rule_version.asc(),
        )
    )

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        statement = statement.where(McpLlmAxisScore.scored_at >= cutoff)

    rows = db.execute(statement).all()
    versions = [
        DecisionRuleVersionRollup(
            decision_rule_version=row.decision_rule_version,
            row_count=int(row.row_count),
            server_count=int(row.server_count),
            first_scored_at=row.first_scored_at,
            last_scored_at=row.last_scored_at,
        )
        for row in rows
    ]

    return DecisionRuleVersionRollupResponse(
        total_rows=sum(version.row_count for version in versions),
        versions=versions,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/api/scoring/decision-rule-versions",
    response_model=DecisionRuleVersionRollupResponse,
)
def get_decision_rule_versions(
    days: Optional[int] = Query(default=None, ge=1),
    db: Session = Depends(get_session),
) -> DecisionRuleVersionRollupResponse:
    return fetch_decision_rule_versions(db=db, days=days)


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

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    decision_rule_version VARCHAR,
                    server_id VARCHAR,
                    scored_at DATETIME,
                    axis_name VARCHAR
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores (
                    id,
                    decision_rule_version,
                    server_id,
                    scored_at,
                    axis_name
                ) VALUES
                    (1, 'v1', 'server-1', '2026-01-01 00:00:00.000000', 'criticality'),
                    (2, 'v1', 'server-1', '2026-01-01 00:01:00.000000', 'exploitability'),
                    (3, 'v1', 'server-2', '2026-01-01 00:02:00.000000', 'criticality'),
                    (4, 'v1', 'server-2', '2026-01-01 00:03:00.000000', 'exploitability'),
                    (5, 'v2', 'server-3', '2026-01-01 00:04:00.000000', 'criticality'),
                    (6, 'v2', 'server-3', '2026-01-01 00:05:00.000000', 'exploitability')
                """
            )
        )

    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/scoring/decision-rule-versions")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_rows"] == 6
    assert payload["versions"][0]["decision_rule_version"] == "v1"
    assert payload["versions"][0]["row_count"] == 4
    assert payload["versions"][0]["server_count"] == 2
    assert payload["versions"][1]["decision_rule_version"] == "v2"
    assert payload["versions"][1]["row_count"] == 2
    assert payload["versions"][1]["server_count"] == 1
    assert "generated_at" in payload

    print("PASS")