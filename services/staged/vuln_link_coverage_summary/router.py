"""router.py -- the service's HTTP surface (one concern, one file).

Exposes `router` (the attribute generate_spine/include_spine mount). Thin:
injects the real Session via Depends(get_session), delegates to logic.py.
Intra-service imports are RELATIVE (`from .logic import ...`) so the module
works identically at services.staged.<name> and services.active.<name> --
promotion moves the directory, never rewrites an import.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink

try:
    from .logic import get_vuln_link_coverage_summary as _coverage_summary
except ImportError:
    try:
        from .logic import summarize_vuln_link_coverage as _coverage_summary
    except ImportError:
        try:
            from .logic import get_coverage_summary as _coverage_summary
        except ImportError:
            from .logic import vuln_link_coverage_summary as _coverage_summary

router = APIRouter(prefix="/api", tags=["vuln-link-coverage-summary"])


class VulnLinkCoverageSummaryResponse(BaseModel):
    total_links: int
    distinct_servers: int
    distinct_advisories: int
    by_match_basis: dict[str, int]
    by_severity: dict[str, int]
    mean_match_confidence: float
    unlinked_servers: int


@router.get(
    "/vuln/links/coverage",
    response_model=VulnLinkCoverageSummaryResponse,
)
def get_vuln_link_coverage(
    severity: str = "SUMMARY",
    db: Session = Depends(get_session),
) -> VulnLinkCoverageSummaryResponse:
    summary = _coverage_summary(db)
    return VulnLinkCoverageSummaryResponse(**summary)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    VulnAdvisory.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)

    test_session_factory = sessionmaker(bind=test_engine)
    test_session = test_session_factory()
    test_session.add_all(
        [
            McpServerRegistry(server_id="server-a"),
            McpServerRegistry(server_id="server-b"),
            McpServerRegistry(server_id="server-unlinked"),
            VulnAdvisory(
                id="ADV-1",
                feed="test",
                source_url="https://example.test/ADV-1",
                summary="First advisory",
                severity="CRITICAL",
            ),
            VulnAdvisory(
                id="ADV-2",
                feed="test",
                source_url="https://example.test/ADV-2",
                summary="Second advisory",
                severity="MEDIUM",
            ),
            VulnLink(
                id=1,
                advisory_id="ADV-1",
                server_id="server-a",
                match_basis="package",
                match_value="example",
                match_confidence=0.9,
            ),
            VulnLink(
                id=2,
                advisory_id="ADV-1",
                server_id="server-b",
                match_basis="package",
                match_value="example",
                match_confidence=0.8,
            ),
            VulnLink(
                id=3,
                advisory_id="ADV-2",
                server_id="server-a",
                match_basis="cve",
                match_value="CVE-TEST",
                match_confidence=0.7,
            ),
        ]
    )
    test_session.commit()
    test_session.close()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = test_session_factory()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln/links/coverage?severity=SUMMARY")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_links"] == 3, payload
        assert payload["by_severity"]["CRITICAL"] == 2, payload
        assert payload["unlinked_servers"] == 1, payload
        assert len(payload["by_match_basis"]) == 2, payload

    print("PASS")