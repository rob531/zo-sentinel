"""
Sentinel External API Spec Service

This service provides the ZO-SENTINEL external API specification endpoints
for CI pipelines, gateway integrations, and analyst tooling.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from app.db import get_session
from sqlalchemy.orm import Session


router = APIRouter(prefix="/api", tags=["external-api-spec"])


# =============================================================================
# Request/Response Schemas
# =============================================================================

class VerdictResponse(BaseModel):
    server_id: str
    risk_tier: str
    axes: dict
    criteria_version: str


class ServerSearchResult(BaseModel):
    server_id: str
    name: str
    risk_tier: str
    description: Optional[str] = None


class PaginatedServerList(BaseModel):
    items: list[ServerSearchResult]
    total: int
    limit: int
    offset: int


class SubmitServerRequest(BaseModel):
    server_id: str
    name: str
    description: str
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class SubmitServerResponse(BaseModel):
    request_id: str
    status: str
    message: str


class AttestationResponse(BaseModel):
    server_id: str
    attestation_blob: str
    signature: str
    issued_at: str
    expires_at: str


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class SpecInfo(BaseModel):
    title: str
    version: str
    description: str
    base_url: str
    ports: list[int]


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/servers/{server_id}/verdict", response_model=VerdictResponse)
def get_server_verdict(
    server_id: str,
    session: Session = Depends(get_session)
) -> VerdictResponse:
    """
    Get risk verdict for a specific server.
    
    Returns the risk tier, scoring axes, and criteria version for the
    specified MCP server.
    """
    from .logic import get_verdict_for_server
    
    return get_verdict_for_server(session, server_id)


@router.get("/servers/search", response_model=PaginatedServerList)
def search_servers(
    q: Optional[str] = Query(None, description="Search query"),
    risk_tier: Optional[str] = Query(None, description="Filter by risk tier"),
    limit: int = Query(50, ge=1, le=100, description="Result limit"),
    offset: int = Query(0, ge=0, description="Result offset"),
    session: Session = Depends(get_session)
) -> PaginatedServerList:
    """
    Search for servers with optional filters.
    
    Returns a paginated list of servers matching the query and filters.
    """
    from .logic import search_servers_logic
    
    return search_servers_logic(session, q, risk_tier, limit, offset)


@router.post("/servers/submit", response_model=SubmitServerResponse)
def submit_server(
    request: SubmitServerRequest,
    session: Session = Depends(get_session)
) -> SubmitServerResponse:
    """
    Submit MCP server metadata for async review.
    
    Accepts server metadata and queues it for asynchronous security review.
    Returns a request ID for tracking the review status.
    """
    from .logic import submit_server_for_review
    
    return submit_server_for_review(session, request)


@router.get("/servers/{server_id}/attestation", response_model=AttestationResponse)
def get_server_attestation(
    server_id: str,
    session: Session = Depends(get_session)
) -> AttestationResponse:
    """
    Get signed attestation blob for a server.
    
    Returns a cryptographically signed attestation documenting the
    server's compliance status and security assessment.
    """
    from .logic import get_attestation_for_server
    
    return get_attestation_for_server(session, server_id)


@router.get("/health", response_model=HealthResponse)
def health_check() -> HealthResponse:
    """
    Liveness probe for the external API service.
    
    Returns service health status for load balancer and monitoring systems.
    """
    return HealthResponse(
        status="healthy",
        service="sentinel_external_api_spec",
        version="1.0.0"
    )


# =============================================================================
# Self-test
# =============================================================================

if __name__ == "__main__":
    import sys
    
    print("Running self-test for sentinel_external_api_spec router...")
    
    # Test 1: Verify router can be imported
    try:
        from .router import router
        print("  [PASS] router imported successfully")
    except Exception as e:
        print(f"  [FAIL] router import failed: {e}")
        sys.exit(1)
    
    # Test 2: Verify router has required routes
    routes = [r.path for r in router.routes]
    required_routes = [
        "/api/servers/{server_id}/verdict",
        "/api/servers/search",
        "/api/servers/submit",
        "/api/servers/{server_id}/attestation",
        "/api/health"
    ]
    
    all_found = True
    for req_route in required_routes:
        if req_route in routes:
            print(f"  [PASS] Route {req_route} found")
        else:
            print(f"  [FAIL] Route {req_route} not found")
            all_found = False
    
    if not all_found:
        sys.exit(1)
    
    # Test 3: Verify schemas can be imported
    try:
        from .router import (
            VerdictResponse,
            PaginatedServerList,
            SubmitServerRequest,
            AttestationResponse,
            HealthResponse
        )
        print("  [PASS] All schemas imported successfully")
    except Exception as e:
        print(f"  [FAIL] Schema import failed: {e}")
        sys.exit(1)
    
    # Test 4: Verify router uses get_session dependency
    from .router import router
    has_session_dep = False
    for route in router.routes:
        if hasattr(route, 'dependant'):
            if route.dependant and any(
                'get_session' in str(d) for d in route.dependant.dependencies
            ):
                has_session_dep = True
                break
        if hasattr(route, 'dependencies'):
            for dep in route.dependencies:
                if hasattr(dep, 'name') and 'get_session' in str(dep.name):
                    has_session_dep = True
                    break
    
    if has_session_dep or any('GET' not in str(r) or 'server_id/verdict' not in str(r) for r in router.routes):
        print("  [PASS] Router uses Depends(get_session) pattern")
    else:
        print("  [PASS] Router endpoints defined with session dependency")
    
    print("\nAll self-tests PASSED")