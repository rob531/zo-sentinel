from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, Org, Perspective

from .contract import OrgRegistryCoverageResponse
from .logic import org_registry_coverage

router = APIRouter(prefix="/api")


def _call_coverage(db: Session, org_id: str | None) -> dict[str, Any]:
    parameters = inspect.signature(org_registry_coverage).parameters
    kwargs: dict[str, Any] = {}

    for parameter_name in ("db", "session", "database"):
        if parameter_name in parameters:
            kwargs[parameter_name] = db
            break

    if "org_id" in parameters:
        kwargs["org_id"] = org_id

    if kwargs:
        return org_registry_coverage(**kwargs)

    return org_registry_coverage(org_id)


@router.get(
    "/orgs/registry-coverage",
    response_model=OrgRegistryCoverageResponse,
    summary="Report MCP registry coverage by organization",
)
def get_org_registry_coverage(
    org_id: str | None = None,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return _call_coverage(db, org_id)


def _self_test() -> None:
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
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    McpServerRegistry.__table__.create(bind=engine)
    Org.__table__.create(bind=engine)
    Perspective.__table__.create(bind=engine)

    now = datetime.now(timezone.utc)
    with testing_session() as db:
        db.add_all(
            [
                Org(id="org-1", name="Organization One", created_at=now),
                Org(id="org-2", name="Organization Two", created_at=now),
            ]
        )
        db.flush()

        assigned_server_ids = ["server-1", "server-2"]
        db.add(
            Perspective(
                id="perspective-1",
                name="Organization One MCP Vantage",
                org_id="org-1",
                facet_filters={
                    "server_id": assigned_server_ids,
                    "server_ids": assigned_server_ids,
                    "mcp_server_ids": assigned_server_ids,
                    "servers": assigned_server_ids,
                },
                created_at=now,
                updated_at=now,
            )
        )
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Critical Server",
                    url="https://server-1.example",
                    risk_tier="CRITICAL",
                    last_assessed=now,
                    meta={
                        "org_id": "org-1",
                        "org_ids": ["org-1"],
                        "perspective_id": "perspective-1",
                        "perspective_ids": ["perspective-1"],
                    },
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="High Risk Server",
                    url="https://server-2.example",
                    risk_tier="HIGH",
                    last_assessed=now,
                    meta={
                        "org_id": "org-1",
                        "org_ids": ["org-1"],
                        "perspective_id": "perspective-1",
                        "perspective_ids": ["perspective-1"],
                    },
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Unassigned Server",
                    url="https://server-3.example",
                    risk_tier="MEDIUM",
                    last_assessed=now,
                    meta={},
                ),
                McpServerRegistry(
                    server_id="server-4",
                    name="Another Unassigned Server",
                    url="https://server-4.example",
                    risk_tier="LOW",
                    last_assessed=now,
                    meta={},
                ),
            ]
        )
        db.commit()

    application = FastAPI()
    application.include_router(router)

    def override_get_session():
        with testing_session() as db:
            yield db

    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get("/api/orgs/registry-coverage")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["total_orgs"] == 2, payload

        rows = payload["orgs"]
        assert len(rows) == 2, payload
        assert rows[0]["server_count"] == 2, payload
        assert rows[0]["critical_count"] == 1, payload
        assert rows[0]["high_risk_count"] == 1, payload
        assert all(
            rows[index]["server_count"] >= rows[index + 1]["server_count"]
            for index in range(len(rows) - 1)
        ), payload

    engine.dispose()


if __name__ == "__main__":
    _self_test()
    print("PASS")