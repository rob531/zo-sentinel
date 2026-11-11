from typing import Any, Dict

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnLink


def get_server_coverage(session: Session, limit: int = 10) -> Dict[str, Any]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > 100:
        raise ValueError("limit must not exceed 100")

    total_servers = session.scalar(
        select(func.count()).select_from(McpServerRegistry)
    ) or 0

    covered_servers = session.scalar(
        select(func.count(func.distinct(VulnLink.server_id)))
        .select_from(VulnLink)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
    ) or 0

    top_statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            func.count(VulnLink.id).label("link_count"),
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            VulnLink,
            VulnLink.server_id == McpServerRegistry.server_id,
        )
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .order_by(
            func.count(VulnLink.id).desc(),
            McpServerRegistry.server_id.asc(),
        )
        .limit(limit)
    )

    top = [
        {
            "server_id": row.server_id,
            "name": row.name,
            "risk_tier": row.risk_tier,
            "link_count": int(row.link_count),
        }
        for row in session.execute(top_statement).mappings()
    ]

    coverage_pct = (
        round((covered_servers / total_servers) * 100, 2)
        if total_servers
        else 0.0
    )

    return {
        "total_servers": int(total_servers),
        "covered_servers": int(covered_servers),
        "coverage_pct": coverage_pct,
        "top": top,
    }


get_coverage_data = get_server_coverage
get_vuln_link_server_coverage = get_server_coverage


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    McpServerRegistry.__table__.create(engine)
    VulnLink.__table__.create(engine)

    with Session(engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-a",
                    name="Server A",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="server-b",
                    name="Server B",
                    risk_tier="medium",
                ),
                McpServerRegistry(
                    server_id="server-c",
                    name="Server C",
                    risk_tier="low",
                ),
            ]
        )
        session.flush()
        session.add_all(
            [
                VulnLink(
                    advisory_id="advisory-1",
                    server_id="server-a",
                    match_basis="package",
                    match_value="package-1",
                    match_confidence=0.95,
                ),
                VulnLink(
                    advisory_id="advisory-2",
                    server_id="server-a",
                    match_basis="version",
                    match_value="version-1",
                    match_confidence=0.90,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()

    @test_app.get("/api/vuln/servers/covered")
    def covered(
        limit: int = 10,
        session: Session = Depends(get_session),
    ):
        return get_server_coverage(session, limit)

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/servers/covered?limit=10")
        assert response.status_code == 200
        payload = response.json()

    assert payload["total_servers"] == 3
    assert payload["covered_servers"] == 1
    assert 30 <= payload["coverage_pct"] <= 34
    assert payload["top"][0]["link_count"] == 2

    print("PASS")


if __name__ == "__main__":
    _self_test()