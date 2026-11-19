from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Org, User

from .logic import get_org_server_coverage_summary

router = APIRouter(prefix="/api", tags=["org_server_coverage_summary"])


class OrgCoverage(BaseModel):
    org_id: str
    org_name: str
    user_count: int
    role_counts: dict[str, int]
    registry_total: int
    tier_counts: dict[str, int]
    avg_trust_score: float | None
    never_assessed: int


class OrgServerCoverageSummaryResponse(BaseModel):
    generated_at: datetime
    orgs: list[OrgCoverage]


@router.get(
    "/orgs/server-coverage",
    response_model=OrgServerCoverageSummaryResponse,
)
def org_server_coverage_summary(
    db: Session = Depends(get_session),
) -> OrgServerCoverageSummaryResponse:
    summary = get_org_server_coverage_summary(db)
    if isinstance(summary, OrgServerCoverageSummaryResponse):
        return summary
    return OrgServerCoverageSummaryResponse.model_validate(summary)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Org.__table__.create(engine)
    User.__table__.create(engine)
    McpServerRegistry.__table__.create(engine)

    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                Org(id="org-1", name="First Organisation"),
                Org(id="org-2", name="Second Organisation"),
                User(
                    id="user-1",
                    org_id="org-1",
                    email="first-admin@example.com",
                    password_hash="test",
                    role="admin",
                ),
                User(
                    id="user-2",
                    org_id="org-1",
                    email="first-member@example.com",
                    password_hash="test",
                    role="member",
                ),
                User(
                    id="user-3",
                    org_id="org-2",
                    email="second-admin@example.com",
                    password_hash="test",
                    role="admin",
                ),
                McpServerRegistry(
                    server_id="server-1",
                    risk_tier="TRUSTED_GENERAL",
                    trust_score=0.9,
                    last_assessed=datetime(2025, 1, 1),
                ),
                McpServerRegistry(
                    server_id="server-2",
                    risk_tier="TRUSTED_GENERAL",
                    trust_score=0.8,
                    last_assessed=datetime(2025, 1, 2),
                ),
                McpServerRegistry(
                    server_id="server-3",
                    risk_tier="UNASSESSED",
                    trust_score=0.5,
                    last_assessed=None,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/orgs/server-coverage")
    response.raise_for_status()
    payload = response.json()

    assert response.status_code == 200
    assert len(payload["orgs"]) == 2
    assert sum(org["user_count"] for org in payload["orgs"]) == 3
    assert all(org["registry_total"] == 3 for org in payload["orgs"])
    assert all(sum(org["tier_counts"].values()) == 3 for org in payload["orgs"])
    assert all(org["never_assessed"] == 1 for org in payload["orgs"])
    assert all(org["avg_trust_score"] is not None for org in payload["orgs"])

    print("PASS")