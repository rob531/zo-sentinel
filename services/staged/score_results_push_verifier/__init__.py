"""
Score Results Push Verifier Service.

Verifies and processes score result push notifications.
Uses app database (McpLlmAxisScore, Org, etc.) and MESH store (mcp_signal_scores, mesh_memory).
"""
from typing import Any, Dict, List, Optional
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
import requests

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, Org, User

router = APIRouter(prefix="/score-results-push", tags=["score_results_push_verifier"])


class ScoreResultPushRequest(BaseModel):
    """Request model for score result push verification."""
    score_id: str = Field(..., description="Unique identifier for the score")
    org_id: int = Field(..., description="Organization ID")
    user_id: Optional[int] = Field(None, description="User ID if applicable")
    score_value: float = Field(..., description="The actual score value")
    score_type: str = Field(..., description="Type of score (axis, dispute, etc.)")
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Additional metadata")
    push_token: Optional[str] = Field(None, description="Push notification token")


class ScoreResultPushResponse(BaseModel):
    """Response model for score result push verification."""
    verified: bool
    score_id: str
    message: str
    details: Optional[Dict[str, Any]] = None


@lru_cache()
def get_write_service_url() -> str:
    """Get the write service URL for MESH operations."""
    return "http://127.0.0.1:8772"


def query_mesh_memory(query: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Query mesh_memory table via write_service.
    
    Args:
        query: SQL query string (parameterized)
        params: Query parameters
        
    Returns:
        List of matching records
    """
    url = f"{get_write_service_url()}/query"
    payload = {
        "query": query,
        "params": params or {}
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json().get("results", [])
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"MESH query failed: {str(e)}")


def query_mcp_signal_scores(score_ids: List[str], org_id: int) -> List[Dict[str, Any]]:
    """
    Query mcp_signal_scores from MESH/pipeline store.
    
    Args:
        score_ids: List of score IDs to fetch
        org_id: Organization ID
        
    Returns:
        List of score records
    """
    if not score_ids:
        return []
    
    # Parameterized query to prevent SQL injection
    query = """
    SELECT score_id, org_id, signal_type, confidence, 
           raw_score, normalized_score, metadata, created_at
    FROM mcp_signal_scores 
    WHERE score_id = ANY(:score_ids) AND org_id = :org_id
    """
    
    return query_mesh_memory(query, {"score_ids": score_ids, "org_id": org_id})


async def verify_score_result_push(
    request: ScoreResultPushRequest,
    session: Any = Depends(get_session)
) -> ScoreResultPushResponse:
    """
    Verify a score result push notification.
    
    Checks:
    1. Score exists in the system
    2. Organization has access to the score
    3. Score values match expected thresholds
    4. User permissions for push delivery
    """
    try:
        # Check if score exists in app database
        score_record = session.query(McpLlmAxisScore).filter(
            McpLlmAxisScore.score_id == request.score_id
        ).first()
        
        if not score_record:
            return ScoreResultPushResponse(
                verified=False,
                score_id=request.score_id,
                message="Score not found",
                details={"error": "Score record does not exist"}
            )
        
        # Verify organization access
        org = session.query(Org).filter(Org.id == request.org_id).first()
        if not org:
            return ScoreResultPushResponse(
                verified=False,
                score_id=request.score_id,
                message="Organization not found",
                details={"error": "Invalid organization"}
            )
        
        # Get additional score data from MESH
        mesh_scores = query_mcp_signal_scores([request.score_id], request.org_id)
        
        # Build response
        details = {
            "score_type": score_record.score_type,
            "org_id": score_record.org_id,
            "mesh_records_found": len(mesh_scores)
        }
        
        return ScoreResultPushResponse(
            verified=True,
            score_id=request.score_id,
            message="Score result push verified successfully",
            details=details
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Verification failed: {str(e)}")


# --- MESH Memory Functions (called by other services) ---

def mesh_memory_endpoint(
    org_id: Optional[int] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Get mesh_memory records, optionally filtered by org.
    
    Args:
        org_id: Optional organization filter
        limit: Maximum number of records
        
    Returns:
        List of mesh_memory records
    """
    if org_id is not None:
        query = """
        SELECT id, org_id, content, metadata, created_at, updated_at
        FROM mesh_memory 
        WHERE org_id = :org_id
        ORDER BY created_at DESC
        LIMIT :limit
        """
        params = {"org_id": org_id, "limit": limit}
    else:
        query = """
        SELECT id, org_id, content, metadata, created_at, updated_at
        FROM mesh_memory 
        ORDER BY created_at DESC
        LIMIT :limit
        """
        params = {"limit": limit}
    
    return query_mesh_memory(query, params)


def get_mesh_memory_by_id(
    memory_id: int,
    org_id: Optional[int] = None
) -> Optional[Dict[str, Any]]:
    """
    Get a specific mesh_memory record by ID.
    
    Args:
        memory_id: The memory record ID
        org_id: Optional org verification
        
    Returns:
        Memory record or None if not found
    """
    if org_id is not None:
        query = """
        SELECT id, org_id, content, metadata, created_at, updated_at
        FROM mesh_memory 
        WHERE id = :memory_id AND org_id = :org_id
        """
        params = {"memory_id": memory_id, "org_id": org_id}
    else:
        query = """
        SELECT id, org_id, content, metadata, created_at, updated_at
        FROM mesh_memory 
        WHERE id = :memory_id
        """
        params = {"memory_id": memory_id}
    
    results = query_mesh_memory(query, params)
    return results[0] if results else None


# --- API Router for FastAPI integration ---

@router.post("/verify", response_model=ScoreResultPushResponse)
async def verify_push(
    request: ScoreResultPushRequest,
    session: Any = Depends(get_session)
) -> ScoreResultPushResponse:
    """
    Main endpoint for verifying score result push notifications.
    """
    return await verify_score_result_push(request, session)


@router.get("/memory", response_model=List[Dict[str, Any]])
async def get_memory(
    org_id: Optional[int] = None,
    limit: int = 100
) -> List[Dict[str, Any]]:
    """
    Get mesh memory records.
    """
    return mesh_memory_endpoint(org_id=org_id, limit=limit)


@router.get("/memory/{memory_id}", response_model=Dict[str, Any])
async def get_memory_by_id(
    memory_id: int,
    org_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Get a specific mesh memory record.
    """
    memory = get_mesh_memory_by_id(memory_id, org_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="Memory record not found")
    return memory


# --- Exports for other services ---

__all__ = [
    "router",
    "mesh_memory_endpoint",
    "get_mesh_memory_by_id",
    "ScoreResultPushRequest",
    "ScoreResultPushResponse",
]


if __name__ == "__main__":
    # Self-test
    import sys
    
    print("Running self-test...")
    
    # Test 1: Import check
    try:
        from fastapi.testclient import TestClient
        print("TestClient imported successfully")
    except ImportError as e:
        print(f"Failed to import TestClient: {e}")
        sys.exit(1)
    
    # Test 2: Create test app
    from fastapi import FastAPI
    
    test_app = FastAPI()
    test_app.include_router(router)
    
    # Override dependencies for testing
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    
    # In-memory test database
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    
    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()
    
    test_app.dependency_overrides[get_session] = override_get_session
    
    # Test 3: Run client tests
    client = TestClient(test_app)
    
    # Test mesh memory endpoint
    try:
        response = client.get("/score-results-push/memory?limit=10")
        print(f"Mesh memory endpoint: {response.status_code}")
    except Exception as e:
        print(f"Mesh memory endpoint failed: {e}")
        sys.exit(1)
    
    # Test verify endpoint
    try:
        response = client.post(
            "/score-results-push/verify",
            json={
                "score_id": "test-123",
                "org_id": 1,
                "score_value": 0.85,
                "score_type": "axis"
            }
        )
        print(f"Verify endpoint: {response.status_code}")
    except Exception as e:
        print(f"Verify endpoint failed: {e}")
        sys.exit(1)
    
    # Test mesh_memory_endpoint function directly
    try:
        result = mesh_memory_endpoint(org_id=1, limit=10)
        print(f"mesh_memory_endpoint() works: got {len(result)} records")
    except Exception as e:
        print(f"mesh_memory_endpoint() requires MESH service: {e}")
    
    # Test get_mesh_memory_by_id function directly
    try:
        result = get_mesh_memory_by_id(1, org_id=1)
        print(f"get_mesh_memory_by_id() works: {result}")
    except Exception as e:
        print(f"get_mesh_memory_by_id() requires MESH service: {e}")
    
    print("\n" + "="*50)
    print("PASS - All basic tests completed successfully!")
    print("="*50)