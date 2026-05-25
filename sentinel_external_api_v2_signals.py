import os

# Read the existing sentinel_external_api.py to understand patterns
existing_file_path = '/home/workspace/zo_sentinel/sentinel_external_api.py'
with open(existing_file_path, 'r') as f:
    existing_content = f.read()

print(existing_content)


from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional, List
from pydantic import BaseModel
import re

# Existing imports and helpers from sentinel_external_api.py
# These will be used via the existing module

# Pydantic models for signal scores
class SignalScore(BaseModel):
    signal_name: str
    score: float
    evidence: Optional[str] = None
    scored_at: Optional[str] = None

# Validation pattern for server_id (32-char hex)
SERVER_ID_PATTERN = re.compile(r'^[a-f0-9]{32}$')

def register_routes(app):
    """
    Register the extended signal endpoint routes with the existing FastAPI app.
    This function patches the existing sentinel_external_api.py with new functionality.
    """
    
    @app.get("/v1/mcp/{server_id}/signals", response_model=List[SignalScore], 
             tags=["MCP Signals"],
             summary="Get all signal scores for an MCP server",
             description="Returns all per-signal scores for a specific MCP server identified by its server_id")
    async def get_mcp_signals(
        server_id: str,
        enforce_rate_limit=Depends(lambda: None),  # Placeholder for actual rate limit dependency
        ws_query=None  # Will be injected from parent module
    ):
        """
        Retrieve all signal scores for a given MCP server.
        
        Args:
            server_id: The 32-character hexadecimal server identifier
            enforce_rate_limit: Dependency to enforce API rate limiting
            ws_query: Web service query helper function
            
        Returns:
            List of SignalScore objects containing signal_name, score, evidence, and scored_at
            
        Raises:
            400: Invalid server_id format
            404: Server not found in registry
        """
        # Validate server_id format (32-char hex)
        if not SERVER_ID_PATTERN.match(server_id):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid server_id format: '{server_id}'. Expected 32-character hexadecimal string."
            )
        
        # Check if server exists in mcp_server_registry first
        # Using ws_query helper from existing module
        if ws_query is None:
            # Fallback: try to import from parent module
            try:
                from sentinel_external_api import ws_query as parent_ws_query
                ws_query = parent_ws_query
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail="Web service query helper not available"
                )
        
        # Query registry to verify server exists
        registry_query = f"""
            SELECT server_id, name, trust_score, verdict 
            FROM mcp_server_registry 
            WHERE server_id = '{server_id}'
        """
        
        registry_result = ws_query(registry_query)
        
        if not registry_result or registry_result.get('count', 0) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"MCP server with server_id '{server_id}' not found in registry"
            )
        
        # Query mcp_signal_scores for all signals associated with this server
        signal_query = f"""
            SELECT signal_name, score, evidence, scored_at
            FROM mcp_signal_scores
            WHERE server_id = '{server_id}'
            ORDER BY signal_name
        """
        
        signal_result = ws_query(signal_query)
        
        if not signal_result or signal_result.get('count', 0) == 0:
            # Server exists but has no signals - return empty list
            return []
        
        # Transform database rows to SignalScore model
        signal_scores = []
        for row in signal_result.get('rows', []):
            signal_score = SignalScore(
                signal_name=row.get('signal_name', ''),
                score=float(row.get('score', 0.0)),
                evidence=row.get('evidence'),
                scored_at=row.get('scored_at')
            )
            signal_scores.append(signal_score)
        
        return signal_scores
    
    # Additional endpoint: GET /v1/mcp/{server_id}/signals/{signal_name}
    # Get a specific signal score for a server
    
    @app.get("/v1/mcp/{server_id}/signals/{signal_name}", 
             response_model=SignalScore,
             tags=["MCP Signals"],
             summary="Get a specific signal score for an MCP server",
             description="Returns the score and details for a specific signal of an MCP server")
    async def get_mcp_signal_detail(
        server_id: str,
        signal_name: str,
        enforce_rate_limit=Depends(lambda: None),
        ws_query=None
    ):
        """
        Retrieve a specific signal score for a given MCP server.
        
        Args:
            server_id: The 32-character hexadecimal server identifier
            signal_name: The name of the signal to retrieve
            enforce_rate_limit: Dependency to enforce API rate limiting
            ws_query: Web service query helper function
            
        Returns:
            SignalScore object containing signal_name, score, evidence, and scored_at
            
        Raises:
            400: Invalid server_id format or signal_name
            404: Server or signal not found
        """
        # Validate server_id format (32-char hex)
        if not SERVER_ID_PATTERN.match(server_id):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid server_id format: '{server_id}'. Expected 32-character hexadecimal string."
            )
        
        # Validate signal_name (alphanumeric, underscore, hyphen, dot allowed)
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', signal_name):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid signal_name format: '{signal_name}'. Use alphanumeric, underscore, hyphen, or dot only."
            )
        
        # Get ws_query from parent or parameter
        if ws_query is None:
            try:
                from sentinel_external_api import ws_query as parent_ws_query
                ws_query = parent_ws_query
            except ImportError:
                raise HTTPException(
                    status_code=500,
                    detail="Web service query helper not available"
                )
        
        # Query for the specific signal
        signal_query = f"""
            SELECT signal_name, score, evidence, scored_at
            FROM mcp_signal_scores
            WHERE server_id = '{server_id}' AND signal_name = '{signal_name}'
        """
        
        signal_result = ws_query(signal_query)
        
        if not signal_result or signal_result.get('count', 0) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Signal '{signal_name}' not found for server '{server_id}'"
            )
        
        row = signal_result['rows'][0]
        return SignalScore(
            signal_name=row.get('signal_name', ''),
            score=float(row.get('score', 0.0)),
            evidence=row.get('evidence'),
            scored_at=row.get('scored_at')
        )
    
    return app

# Alternative: Standalone router that can be mounted
def create_signals_router(ws_query_func):
    """
    Create a standalone router with signal endpoints.
    Use this if mounting as a separate router.
    
    Args:
        ws_query_func: The ws_query helper function from the parent module
        
    Returns:
        APIRouter with signal endpoints
    """
    router = APIRouter(prefix="/v1/mcp", tags=["MCP Signals"])
    
    def _ws_query(query):
        return ws_query_func(query)
    
    @router.get("/{server_id}/signals", response_model=List[SignalScore])
    async def list_server_signals(
        server_id: str,
        enforce_rate_limit=Depends(lambda: None)
    ):
        """List all signal scores for an MCP server."""
        if not SERVER_ID_PATTERN.match(server_id):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid server_id format: '{server_id}'. Expected 32-character hexadecimal string."
            )
        
        # Verify server exists
        registry_result = _ws_query(f"""
            SELECT server_id FROM mcp_server_registry 
            WHERE server_id = '{server_id}'
        """)
        
        if not registry_result or registry_result.get('count', 0) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"MCP server with server_id '{server_id}' not found in registry"
            )
        
        # Get signals
        signal_result = _ws_query(f"""
            SELECT signal_name, score, evidence, scored_at
            FROM mcp_signal_scores
            WHERE server_id = '{server_id}'
            ORDER BY signal_name
        """)
        
        if not signal_result or signal_result.get('count', 0) == 0:
            return []
        
        return [
            SignalScore(
                signal_name=row.get('signal_name', ''),
                score=float(row.get('score', 0.0)),
                evidence=row.get('evidence'),
                scored_at=row.get('scored_at')
            )
            for row in signal_result.get('rows', [])
        ]
    
    @router.get("/{server_id}/signals/{signal_name}", response_model=SignalScore)
    async def get_single_signal(
        server_id: str,
        signal_name: str,
        enforce_rate_limit=Depends(lambda: None)
    ):
        """Get a specific signal score for an MCP server."""
        if not SERVER_ID_PATTERN.match(server_id):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid server_id format: '{server_id}'. Expected 32-character hexadecimal string."
            )
        
        result = _ws_query(f"""
            SELECT signal_name, score, evidence, scored_at
            FROM mcp_signal_scores
            WHERE server_id = '{server_id}' AND signal_name = '{signal_name}'
        """)
        
        if not result or result.get('count', 0) == 0:
            raise HTTPException(
                status_code=404,
                detail=f"Signal '{signal_name}' not found for server '{server_id}'"
            )
        
        row = result['rows'][0]
        return SignalScore(
            signal_name=row.get('signal_name', ''),
            score=float(row.get('score', 0.0)),
            evidence=row.get('evidence'),
            scored_at=row.get('scored_at')
        )
    
    return router

if __name__ == '__main__':
    print("sentinel_external_api_v2_signals.py - Signal endpoint extension for sentinel_external_api")
    print("Import this module and call register_routes(app) to add the new endpoints to existing FastAPI app.")
    print("Or use create_signals_router(ws_query_func) to create a standalone router.")