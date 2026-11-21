"""HTTP surface for vulnerability-link provenance."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_vuln_link_provenance
except ImportError:
    from .logic import get_link_provenance as get_vuln_link_provenance


router = APIRouter(prefix="/api", tags=["vuln-link-provenance"])


class BasisProvenance(BaseModel):
    basis: str
    count: int
    avg_confidence: float


class FeedProvenance(BaseModel):
    feed: str
    count: int
    distinct_servers: int


class LowConfidenceLink(BaseModel):
    server_id: str
    advisory_id: int
    match_value: str
    match_confidence: float


class VulnLinkProvenanceResponse(BaseModel):
    total_links: int
    by_basis: list[BasisProvenance]
    by_feed: list[FeedProvenance]
    low_confidence: list[LowConfidenceLink]


@router.get(
    "/vuln/links/provenance",
    response_model=VulnLinkProvenanceResponse,
)
def vuln_link_provenance(
    db: Session = Depends(get_session),
) -> VulnLinkProvenanceResponse:
    result = get_vuln_link_provenance(db)
    return VulnLinkProvenanceResponse(**result)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import McpServerRegistry, VulnAdvisory, VulnLink

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(
        engine,
        tables=[
            McpServerRegistry.__table__,
            VulnAdvisory.__table__,
            VulnLink.__table__,
        ],
    )
    Session = sessionmaker(bind=engine)

    with Session() as session:
        session.add_all(
            [
                McpServerRegistry(server_id="srv-a", name="Server A"),
                McpServerRegistry(server_id="srv-b", name="Server B"),
                McpServerRegistry(server_id="srv-c", name="Server C"),
            ]
        )
        session.add(
            VulnAdvisory(
                id=1,
                feed="ossindex",
                severity="high",
                ecosystem="pypi",
                package="requests",
                source_url="https://example.invalid/advisory/1",
            )
        )
        session.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id=1,
                    server_id="srv-a",
                    match_basis="package_name",
                    match_value="requests",
                    match_confidence=0.20,
                ),
                VulnLink(
                    id=2,
                    advisory_id=1,
                    server_id="srv-b",
                    match_basis="package_name",
                    match_value="requests",
                    match_confidence=0.40,
                ),
                VulnLink(
                    id=3,
                    advisory_id=1,
                    server_id="srv-c",
                    match_basis="package_name",
                    match_value="requests",
                    match_confidence=0.95,
                ),
            ]
        )
        session.commit()

        application = FastAPI()
        application.include_router(router)
        application.dependency_overrides[get_session] = lambda: session

        with TestClient(application) as client:
            response = client.get("/api/vuln/links/provenance")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_links"] == 3
        basis = next(
            item for item in payload["by_basis"] if item["basis"] == "package_name"
        )
        assert basis["count"] == 3
        confidences = [
            item["match_confidence"] for item in payload["low_confidence"]
        ]
        assert confidences == sorted(confidences)

    print("PASS")


if __name__ == "__main__":
    _self_test()