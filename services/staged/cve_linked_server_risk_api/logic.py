from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


class LinkedServerRow(BaseModel):
    server_id: str
    name: Optional[str]
    risk_tier: Optional[str]
    advisory_count: int
    advisory_ids: List[str]
    max_severity: Optional[str]
    max_match_confidence: Optional[float]


class LinkedServersResponse(BaseModel):
    servers: int
    rows: List[LinkedServerRow]


def get_linked_servers(
    session: Session,
    severity: Optional[str] = None,
    risk_tier: Optional[str] = None,
) -> Dict[str, object]:
    filters = []
    if severity is not None:
        filters.append(VulnAdvisory.severity == severity)
    if risk_tier is not None:
        filters.append(McpServerRegistry.risk_tier == risk_tier)

    aggregate_query = (
        select(
            VulnLink.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
            func.count(func.distinct(VulnLink.advisory_id)).label("advisory_count"),
            func.max(VulnAdvisory.severity).label("max_severity"),
            func.max(VulnLink.match_confidence).label("max_match_confidence"),
        )
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
    )
    advisory_query = (
        select(VulnLink.server_id, VulnLink.advisory_id)
        .join(VulnAdvisory, VulnLink.advisory_id == VulnAdvisory.id)
        .join(McpServerRegistry, VulnLink.server_id == McpServerRegistry.server_id)
        .distinct()
    )

    if filters:
        aggregate_query = aggregate_query.where(*filters)
        advisory_query = advisory_query.where(*filters)

    aggregate_query = aggregate_query.group_by(
        VulnLink.server_id,
        McpServerRegistry.name,
        McpServerRegistry.risk_tier,
    ).order_by(
        func.count(func.distinct(VulnLink.advisory_id)).desc(),
        VulnLink.server_id.asc(),
    )

    aggregate_rows = session.execute(aggregate_query).all()
    advisory_rows = session.execute(advisory_query).all()

    advisory_ids_by_server: Dict[str, List[str]] = {}
    for server_id, advisory_id in advisory_rows:
        server_key = str(server_id)
        advisory_ids_by_server.setdefault(server_key, [])
        advisory_id_value = str(advisory_id)
        if advisory_id_value not in advisory_ids_by_server[server_key]:
            advisory_ids_by_server[server_key].append(advisory_id_value)

    rows: List[LinkedServerRow] = []
    for server_id, name, risk_tier, advisory_count, max_severity, max_match_confidence in aggregate_rows:
        server_key = str(server_id)
        confidence = (
            float(max_match_confidence)
            if max_match_confidence is not None
            else None
        )
        rows.append(
            LinkedServerRow(
                server_id=server_key,
                name=name,
                risk_tier=risk_tier,
                advisory_count=int(advisory_count),
                advisory_ids=advisory_ids_by_server.get(server_key, []),
                max_severity=max_severity,
                max_match_confidence=confidence,
            )
        )

    return {"servers": len(rows), "rows": rows}


def get_cve_linked_servers(
    session: Session,
    severity: Optional[str] = None,
    risk_tier: Optional[str] = None,
) -> Dict[str, object]:
    return get_linked_servers(session, severity=severity, risk_tier=risk_tier)


router = APIRouter(prefix="/api")


@router.get("/cve/linked-servers", response_model=LinkedServersResponse)
def linked_servers_endpoint(
    session: Session = Depends(get_session),
    severity: Optional[str] = Query(default=None),
    risk_tier: Optional[str] = Query(default=None),
) -> Dict[str, object]:
    return get_linked_servers(session, severity=severity, risk_tier=risk_tier)


app = FastAPI(title="CVE Linked Server Risk API")
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)
    VulnAdvisory.__table__.create(engine)
    VulnLink.__table__.create(engine)

    with TestingSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-a",
                    name="Server A",
                    risk_tier="high",
                    trust_score=0.9,
                    verdict="review",
                ),
                McpServerRegistry(
                    server_id="server-b",
                    name="Server B",
                    risk_tier="medium",
                    trust_score=0.8,
                    verdict="monitor",
                ),
                VulnAdvisory(
                    id="ADV-1",
                    feed="test",
                    source_url="https://example.test/ADV-1",
                    severity="high",
                    package="package-one",
                    ecosystem="npm",
                    summary="First advisory",
                ),
                VulnAdvisory(
                    id="ADV-2",
                    feed="test",
                    source_url="https://example.test/ADV-2",
                    severity="medium",
                    package="package-two",
                    ecosystem="npm",
                    summary="Second advisory",
                ),
                VulnLink(
                    id=1,
                    advisory_id="ADV-1",
                    server_id="server-a",
                    match_basis="package",
                    match_value="package-one",
                    match_confidence=0.95,
                ),
                VulnLink(
                    id=2,
                    advisory_id="ADV-2",
                    server_id="server-a",
                    match_basis="package",
                    match_value="package-two",
                    match_confidence=0.90,
                ),
                VulnLink(
                    id=3,
                    advisory_id="ADV-1",
                    server_id="server-b",
                    match_basis="package",
                    match_value="package-one",
                    match_confidence=0.75,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/cve/linked-servers")
    assert response.status_code == 200
    payload = response.json()
    assert payload["servers"] == 2
    assert payload["rows"][0]["advisory_count"] == 2
    print("PASS")


if __name__ == "__main__":
    _self_test()