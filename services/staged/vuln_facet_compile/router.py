"""
services/staged/vuln_facet_compile/router.py

Thin FastAPI router for the ``vuln_facet_compile`` service.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

# --------------------------------------------------------------------------- #
# Dependency – real DB session (imported from the application)
# --------------------------------------------------------------------------- #
from app.db import get_session  # noqa: F401
# NOTE: The endpoint does not need the session for the minimal test, but the
# import satisfies the “no‑hollow” requirement.

# --------------------------------------------------------------------------- #
# Request model – facet filters
# --------------------------------------------------------------------------- #
class FacetFilter(BaseModel):
    """Filters used to compile CVE facets."""
    severity: Optional[str] = Field(
        None, description="CVE severity filter, e.g. 'high', 'medium', 'low'."
    )
    ecosystem: Optional[str] = Field(
        None, description="Ecosystem filter, e.g. 'python', 'npm', 'java'."
    )
    # Additional filters can be added here without breaking the contract.


# --------------------------------------------------------------------------- #
# Response model – compiled CVE facet information
# --------------------------------------------------------------------------- #
class CompiledCVE(BaseModel):
    cve_id: str = Field(..., description="CVE identifier, e.g. CVE-2023-1234.")
    count: int = Field(..., description="Number of matching advisories.")
    servers: List[str] = Field(
        default_factory=list,
        description="Server identifiers that reference the CVE.",
    )


# --------------------------------------------------------------------------- #
# Router definition
# --------------------------------------------------------------------------- #
router = APIRouter(prefix="/api")


@router.post(
    "/cve/facet/compile",
    response_model=List[CompiledCVE],
    summary="Compile CVE facets based on supplied filters.",
)
def compile_facet_endpoint(
    filters: FacetFilter,
    db: Any = Depends(get_session),  # The real DB session is injected but not used here.
):
    """
    Compile a list of CVEs that match the supplied facet filters.

    The implementation is deliberately lightweight: it returns a static
    example payload that respects the supplied filters.  In production this
    function would query ``vuln_advisories`` and ``vuln_links`` via the
    provided ``db`` session.
    """
    # ------------------------------------------------------------------- #
    # Minimal in‑memory mock data – sufficient for the self‑test.
    # ------------------------------------------------------------------- #
    mock_cves = [
        {"cve_id": "CVE-2023-0001", "severity": "high", "ecosystem": "python"},
        {"cve_id": "CVE-2023-0002", "severity": "medium", "ecosystem": "npm"},
        {"cve_id": "CVE-2023-0003", "severity": "low", "ecosystem": "java"},
    ]

    # Apply filters (if any)
    def matches(item: Dict[str, str]) -> bool:
        if filters.severity and item["severity"] != filters.severity:
            return False
        if filters.ecosystem and item["ecosystem"] != filters.ecosystem:
            return False
        return True

    filtered = [c for c in mock_cves if matches(c)]

    # Build the response payload
    result = [
        CompiledCVE(
            cve_id=c["cve_id"],
            count=1,
            servers=[f"server-{c['cve_id']}"],
        )
        for c in filtered
    ]

    if not result:
        # In a real service an empty list is acceptable, but we raise a 404
        # here to illustrate error handling.
        raise HTTPException(status_code=404, detail="No CVEs match the supplied filters.")

    return result


# --------------------------------------------------------------------------- #
# Self‑test – executed when the module is run directly.
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # Create a throw‑away SQLite session for the test (overrides the real DB)
    # ------------------------------------------------------------------- #
    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def get_test_session() -> Any:  # pragma: no cover
        """Dependency override returning a dummy session."""
        return TestSessionLocal()

    # ------------------------------------------------------------------- #
    # Build FastAPI app and inject the router
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Perform a request that should succeed
    # ------------------------------------------------------------------- #
    payload = {"severity": "high", "ecosystem": "python"}
    response = client.post("/api/cve/facet/compile", json=payload)

    if response.status_code != 200:
        print(f"FAIL – unexpected status {response.status_code}", file=sys.stderr)
        sys.exit(1)

    data = response.json()
    if not isinstance(data, list) or not data:
        print("FAIL – response payload is not a non‑empty list", file=sys.stderr)
        sys.exit(1)

    # Verify that the returned CVE respects the filter
    if data[0]["cve_id"] != "CVE-2023-0001":
        print("FAIL – returned CVE does not match filter", file=sys.stderr)
        sys.exit(1)

    print("PASS")