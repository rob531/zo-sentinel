from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnLink


class CoveredServer(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    link_count: int


class VulnServerCoverageResponse(BaseModel):
    total_servers: int
    covered_servers: int
    coverage_pct: float
    top: List[CoveredServer]


def get_vuln_server_coverage(session, limit: int) -> VulnServerCoverageResponse:
    total_servers = session.execute(
        select(func.count()).select_from(McpServerRegistry)
    ).scalar_one()

    covered_servers = session.execute(
        select(func.count(func.distinct(VulnLink.server_id)))
        .select_from(VulnLink)
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == VulnLink.server_id,
        )
    ).scalar_one()

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            func.count(VulnLink.id).label("link_count"),
        )
        .select_from(McpServerRegistry)
        .outerjoin(VulnLink, VulnLink.server_id == McpServerRegistry.server_id)
        .group_by(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        )
        .order_by(func.count(VulnLink.id).desc(), McpServerRegistry.server_id.asc())
        .limit(limit)
    ).all()

    coverage_pct = (
        round((covered_servers / total_servers) * 100, 2)
        if total_servers
        else 0.0
    )

    return VulnServerCoverageResponse(
        total_servers=total_servers,
        covered_servers=covered_servers,
        coverage_pct=coverage_pct,
        top=[
            CoveredServer(
                server_id=row.server_id,
                name=row.name,
                risk_tier=row.risk_tier,
                link_count=row.link_count,
            )
            for row in rows
        ],
    )


app = FastAPI()


@app.get(
    "/api/vuln/servers/covered",
    response_model=VulnServerCoverageResponse,
)
def vuln_servers_covered(
    limit: int = Query(default=10, ge=1),
    session=Depends(get_session),
) -> VulnServerCoverageResponse:
    return get_vuln_server_coverage(session, limit)


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )

    McpServerRegistry.__table__.create(bind=engine)
    VulnLink.__table__.create(bind=engine)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestSession() as session:
            session.add_all(
                [
                    McpServerRegistry(
                        server_id="server-covered",
                        name="Covered Server",
                        risk_tier="high",
                    ),
                    McpServerRegistry(
                        server_id="server-uncovered-1",
                        name="Uncovered Server One",
                        risk_tier="medium",
                    ),
                    McpServerRegistry(
                        server_id="server-uncovered-2",
                        name="Uncovered Server Two",
                        risk_tier="low",
                    ),
                ]
            )
            session.add_all(
                [
                    VulnLink(
                        advisory_id=1,
                        server_id="server-covered",
                        match_basis="cve",
                        match_value="CVE-2024-0001",
                        match_confidence=0.95,
                        linked_at=datetime(2024, 1, 1, 0, 0, 0),
                    ),
                    VulnLink(
                        advisory_id=2,
                        server_id="server-covered",
                        match_basis="cve",
                        match_value="CVE-2024-0002",
                        match_confidence=0.90,
                        linked_at=datetime(2024, 1, 2, 0, 0, 0),
                    ),
                ]
            )
            session.commit()

        with TestClient(app) as client:
            response = client.get("/api/vuln/servers/covered", params={"limit": 3})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_servers"] == 3, payload
            assert payload["covered_servers"] == 1, payload
            assert 30 <= payload["coverage_pct"] <= 34, payload
            assert payload["top"][0]["server_id"] == "server-covered", payload
            assert payload["top"][0]["link_count"] == 2, payload

        print("PASS")
    finally:
        app.dependency_overrides.pop(get_session, None)
        engine.dispose()


if __name__ == "__main__":
    run_self_test()