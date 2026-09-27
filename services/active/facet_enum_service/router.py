# deps: fastapi, sqlalchemy, pydantic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["facet_enum_service"])


class FacetValue(BaseModel):
    value: str | None
    count: int


class FacetResponse(BaseModel):
    facet: str
    values: list[FacetValue]


SUPPORTED_FACETS = {"axis_name", "risk_tier", "registry_source", "verdict", "trust_score_bucket"}


def _enum_axis_name(session: Session) -> list[FacetValue]:
    stmt = (
        select(McpLlmAxisScore.axis_name.label("value"), func.count().label("count"))
        .group_by(McpLlmAxisScore.axis_name)
    )
    return [FacetValue(value=row.value, count=row.count) for row in session.execute(stmt)]


def _enum_risk_tier(session: Session) -> list[FacetValue]:
    stmt = (
        select(McpServerRegistry.risk_tier.label("value"), func.count().label("count"))
        .group_by(McpServerRegistry.risk_tier)
    )
    return [FacetValue(value=row.value, count=row.count) for row in session.execute(stmt)]


def _enum_registry_source(session: Session) -> list[FacetValue]:
    stmt = (
        select(McpServerRegistry.registry_source.label("value"), func.count().label("count"))
        .group_by(McpServerRegistry.registry_source)
    )
    return [FacetValue(value=row.value, count=row.count) for row in session.execute(stmt)]


def _enum_verdict(session: Session) -> list[FacetValue]:
    stmt = (
        select(McpServerRegistry.verdict.label("value"), func.count().label("count"))
        .group_by(McpServerRegistry.verdict)
    )
    return [FacetValue(value=row.value, count=row.count) for row in session.execute(stmt)]


def _enum_trust_score_bucket(session: Session) -> list[FacetValue]:
    from sqlalchemy import case
    stmt = (
        select(
            case(
                (McpServerRegistry.trust_score < 33, "low"),
                (McpServerRegistry.trust_score < 66, "medium"),
                else_="high",
            ).label("value"),
            func.count().label("count"),
        )
        .group_by(
            case(
                (McpServerRegistry.trust_score < 33, "low"),
                (McpServerRegistry.trust_score < 66, "medium"),
                else_="high",
            )
        )
    )
    return [FacetValue(value=row.value, count=row.count) for row in session.execute(stmt)]


def get_facet_values(session: Session, facet_name: str) -> list[FacetValue]:
    if facet_name == "axis_name":
        return _enum_axis_name(session)
    elif facet_name == "risk_tier":
        return _enum_risk_tier(session)
    elif facet_name == "registry_source":
        return _enum_registry_source(session)
    elif facet_name == "verdict":
        return _enum_verdict(session)
    elif facet_name == "trust_score_bucket":
        return _enum_trust_score_bucket(session)
    return []


@router.get("/facets/{facet_name}", response_model=FacetResponse)
def get_facet(facet_name: str, session: Session = Depends(get_session)) -> FacetResponse:
    if facet_name not in SUPPORTED_FACETS:
        raise HTTPException(status_code=404, detail=f"Facet '{facet_name}' not found")
    values = get_facet_values(session, facet_name)
    return FacetResponse(facet=facet_name, values=values)


@router.get("/facets", response_model=list[str])
def list_facets() -> list[str]:
    return sorted(SUPPORTED_FACETS)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE mcp_server_registry (
                server_id TEXT PRIMARY KEY,
                name TEXT,
                registry_source TEXT,
                verdict TEXT,
                trust_score REAL
            )
        """))
        conn.execute(text("""
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id TEXT,
                axis_name TEXT
            )
        """))
        conn.execute(text("""
            INSERT INTO mcp_server_registry VALUES
                ('s1', 'Server One', 'npm', 'approved', 85.0),
                ('s2', 'Server Two', 'github', 'pending', 45.0),
                ('s3', 'Server Three', 'manual', 'rejected', 20.0)
        """))
        conn.execute(text("""
            INSERT INTO mcp_llm_axis_scores VALUES
                (1, 's1', 'security'),
                (2, 's2', 'reliability'),
                (3, 's3', 'compliance')
        """))

    TestSession = sessionmaker(bind=engine)

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)

    assert client.get("/api/facets").json() == sorted(SUPPORTED_FACETS)

    for facet in ["axis_name", "risk_tier", "registry_source", "verdict", "trust_score_bucket"]:
        resp = client.get(f"/api/facets/{facet}")
        assert resp.status_code == 200, f"{facet}: {resp.status_code}"
        data = resp.json()
        assert data["facet"] == facet
        assert len(data["values"]) > 0, f"No values for {facet}"

    resp = client.get("/api/facets/unknown_facet")
    assert resp.status_code == 404

    print("PASS")
