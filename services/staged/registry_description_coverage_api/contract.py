from typing import List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    total: int
    with_description: int
    with_url: int
    with_trust_score: int
    description_coverage_pct: float


class DescriptionCoverageTotals(BaseModel):
    servers: int
    with_description: int


class DescriptionCoverageResponse(BaseModel):
    sources: List[RegistrySourceCoverage]
    totals: DescriptionCoverageTotals


COVERAGE_QUERY = text(
    """
    SELECT
        registry_source,
        COUNT(*) AS total,
        COUNT(CASE WHEN description IS NOT NULL AND description <> '' THEN 1 END)
            AS with_description,
        COUNT(CASE WHEN url IS NOT NULL AND url <> '' THEN 1 END)
            AS with_url,
        COUNT(CASE WHEN trust_score IS NOT NULL THEN 1 END)
            AS with_trust_score
    FROM mcp_server_registry
    GROUP BY registry_source
    ORDER BY registry_source
    """
)


def get_description_coverage(
    session=Depends(get_session),
) -> DescriptionCoverageResponse:
    rows = session.execute(COVERAGE_QUERY).mappings().all()

    sources = []
    total_servers = 0
    total_with_description = 0

    for row in rows:
        total = int(row["total"])
        with_description = int(row["with_description"])
        coverage = round((with_description * 100.0 / total) if total else 0.0, 2)

        sources.append(
            RegistrySourceCoverage(
                registry_source=str(row["registry_source"]),
                total=total,
                with_description=with_description,
                with_url=int(row["with_url"]),
                with_trust_score=int(row["with_trust_score"]),
                description_coverage_pct=coverage,
            )
        )
        total_servers += total
        total_with_description += with_description

    return DescriptionCoverageResponse(
        sources=sources,
        totals=DescriptionCoverageTotals(
            servers=total_servers,
            with_description=total_with_description,
        ),
    )


app = FastAPI()
app.add_api_route(
    "/api/registry/description-coverage",
    get_description_coverage,
    methods=["GET"],
    response_model=DescriptionCoverageResponse,
)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(bind=engine)

    session = TestingSessionLocal()
    try:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="contract-npm-1",
                    registry_source="npm",
                    url="https://registry.npmjs.org/example-one",
                    description="Example package",
                    trust_score=0.9,
                ),
                McpServerRegistry(
                    server_id="contract-npm-2",
                    registry_source="npm",
                    url="https://registry.npmjs.org/example-two",
                    description="",
                    trust_score=None,
                ),
                McpServerRegistry(
                    server_id="contract-pypi-1",
                    registry_source="pypi",
                    url=None,
                    description="Example Python package",
                    trust_score=0.8,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        override_session = TestingSessionLocal()
        try:
            yield override_session
        finally:
            override_session.close()

    test_app = FastAPI()
    test_app.add_api_route(
        "/api/registry/description-coverage",
        get_description_coverage,
        methods=["GET"],
        response_model=DescriptionCoverageResponse,
    )
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/registry/description-coverage")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert len(payload["sources"]) == 2, payload
        assert payload["totals"]["servers"] == 3, payload

        npm = next(
            source
            for source in payload["sources"]
            if source["registry_source"] == "npm"
        )
        assert npm["with_description"] == 1, payload
        assert npm["total"] == 2, payload
        assert npm["description_coverage_pct"] == 50.0, payload
        assert payload["totals"]["with_description"] == 2, payload
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()