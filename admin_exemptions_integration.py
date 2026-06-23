"""
admin_exemptions_integration.py

Integration utility to wire admin_exemptions.html to the backend mcp_exemptions table.
Uses write_service on :8772 for DB access per section 5 wiring rules.
"""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from fastapi import APIRouter, HTTPException, Query
import logging

logger = logging.getLogger(__name__)

# Schema models
class ExemptionBase(BaseModel):
    exemption_name: str = Field(..., description="Name of the exemption")
    exemption_type: str = Field(..., description="Type of exemption")
    resource_pattern: Optional[str] = Field(None, description="Resource pattern to match")
    description: Optional[str] = Field(None, description="Description of exemption")
    is_active: bool = Field(default=True, description="Whether exemption is active")
    priority: int = Field(default=0, description="Priority order")


class ExemptionCreate(ExemptionBase):
    pass


class ExemptionUpdate(BaseModel):
    exemption_name: Optional[str] = None
    exemption_type: Optional[str] = None
    resource_pattern: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None


class ExemptionResponse(ExemptionBase):
    exemption_id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# Create router
router = APIRouter(prefix="/api/admin/exemptions", tags=["admin_exemptions"])


def get_write_service():
    """Get write service connection on port 8772."""
    # Per section 5 wiring rules - use write_service on 8772
    from shared_services.service_registry import get_service_connection
    return get_service_connection("write_service", port=8772)


@router.get("/", response_model=List[ExemptionResponse])
async def list_exemptions(
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    exemption_type: Optional[str] = Query(None, description="Filter by type"),
    limit: int = Query(100, ge=1, le=500, description="Max results"),
    offset: int = Query(0, ge=0, description="Offset for pagination")
) -> List[ExemptionResponse]:
    """
    List all exemptions with optional filtering.
    """
    try:
        write_service = get_write_service()
        
        query = "SELECT * FROM mcp_exemptions WHERE 1=1"
        params = []
        
        if is_active is not None:
            query += " AND is_active = %s"
            params.append(is_active)
        
        if exemption_type:
            query += " AND exemption_type = %s"
            params.append(exemption_type)
        
        query += " ORDER BY priority DESC, exemption_id"
        query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        
        result = await write_service.execute_query(query, params)
        return [ExemptionResponse(**row) for row in result]
    
    except Exception as e:
        logger.error(f"Error listing exemptions: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list exemptions: {str(e)}")


@router.get("/{exemption_id}", response_model=ExemptionResponse)
async def get_exemption(exemption_id: int) -> ExemptionResponse:
    """
    Get a specific exemption by ID.
    """
    try:
        write_service = get_write_service()
        
        query = "SELECT * FROM mcp_exemptions WHERE exemption_id = %s"
        result = await write_service.execute_query(query, (exemption_id,))
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Exemption {exemption_id} not found")
        
        return ExemptionResponse(**result[0])
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting exemption {exemption_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get exemption: {str(e)}")


@router.post("/", response_model=ExemptionResponse, status_code=201)
async def create_exemption(exemption: ExemptionCreate) -> ExemptionResponse:
    """
    Create a new exemption.
    """
    try:
        write_service = get_write_service()
        
        query = """
            INSERT INTO mcp_exemptions 
            (exemption_name, exemption_type, resource_pattern, description, is_active, priority, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW())
            RETURNING *
        """
        params = (
            exemption.exemption_name,
            exemption.exemption_type,
            exemption.resource_pattern,
            exemption.description,
            exemption.is_active,
            exemption.priority
        )
        
        result = await write_service.execute_query(query, params)
        
        if not result:
            raise HTTPException(status_code=500, detail="Failed to create exemption")
        
        return ExemptionResponse(**result[0])
    
    except Exception as e:
        logger.error(f"Error creating exemption: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create exemption: {str(e)}")


@router.put("/{exemption_id}", response_model=ExemptionResponse)
async def update_exemption(exemption_id: int, exemption: ExemptionUpdate) -> ExemptionResponse:
    """
    Update an existing exemption.
    """
    try:
        write_service = get_write_service()
        
        # Build dynamic update query
        updates = []
        params = []
        
        if exemption.exemption_name is not None:
            updates.append("exemption_name = %s")
            params.append(exemption.exemption_name)
        if exemption.exemption_type is not None:
            updates.append("exemption_type = %s")
            params.append(exemption.exemption_type)
        if exemption.resource_pattern is not None:
            updates.append("resource_pattern = %s")
            params.append(exemption.resource_pattern)
        if exemption.description is not None:
            updates.append("description = %s")
            params.append(exemption.description)
        if exemption.is_active is not None:
            updates.append("is_active = %s")
            params.append(exemption.is_active)
        if exemption.priority is not None:
            updates.append("priority = %s")
            params.append(exemption.priority)
        
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")
        
        updates.append("updated_at = NOW()")
        params.append(exemption_id)
        
        query = f"""
            UPDATE mcp_exemptions 
            SET {', '.join(updates)}
            WHERE exemption_id = %s
            RETURNING *
        """
        
        result = await write_service.execute_query(query, params)
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Exemption {exemption_id} not found")
        
        return ExemptionResponse(**result[0])
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating exemption {exemption_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update exemption: {str(e)}")


@router.delete("/{exemption_id}", status_code=204)
async def delete_exemption(exemption_id: int) -> None:
    """
    Delete an exemption.
    """
    try:
        write_service = get_write_service()
        
        query = "DELETE FROM mcp_exemptions WHERE exemption_id = %s RETURNING exemption_id"
        result = await write_service.execute_query(query, (exemption_id,))
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Exemption {exemption_id} not found")
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting exemption {exemption_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete exemption: {str(e)}")


@router.patch("/{exemption_id}/toggle", response_model=ExemptionResponse)
async def toggle_exemption(exemption_id: int) -> ExemptionResponse:
    """
    Toggle the active status of an exemption.
    """
    try:
        write_service = get_write_service()
        
        query = """
            UPDATE mcp_exemptions 
            SET is_active = NOT is_active, updated_at = NOW()
            WHERE exemption_id = %s
            RETURNING *
        """
        result = await write_service.execute_query(query, (exemption_id,))
        
        if not result:
            raise HTTPException(status_code=404, detail=f"Exemption {exemption_id} not found")
        
        return ExemptionResponse(**result[0])
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error toggling exemption {exemption_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to toggle exemption: {str(e)}")


@router.get("/types/list", response_model=List[str])
async def list_exemption_types() -> List[str]:
    """
    Get list of distinct exemption types.
    """
    try:
        write_service = get_write_service()
        
        query = "SELECT DISTINCT exemption_type FROM mcp_exemptions ORDER BY exemption_type"
        result = await write_service.execute_query(query)
        
        return [row["exemption_type"] for row in result]
    
    except Exception as e:
        logger.error(f"Error listing exemption types: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list exemption types: {str(e)}")


# Utility functions for direct access
async def get_all_exemptions() -> List[dict]:
    """Get all exemptions as dicts."""
    exemptions = await list_exemptions(limit=500)
    return [e.model_dump() for e in exemptions]


async def get_active_exemptions() -> List[dict]:
    """Get all active exemptions as dicts."""
    exemptions = await list_exemptions(is_active=True, limit=500)
    return [e.model_dump() for e in exemptions]


async def get_exemption_by_id(exemption_id: int) -> Optional[dict]:
    """Get exemption by ID as dict."""
    try:
        exemption = await get_exemption(exemption_id)
        return exemption.model_dump()
    except HTTPException:
        return None


async def check_resource_exempted(resource: str, exemption_type: str = None) -> bool:
    """
    Check if a resource matches any active exemption pattern.
    Useful for validation before creating new exemptions.
    """
    try:
        write_service = get_write_service()
        
        query = """
            SELECT COUNT(*) as count FROM mcp_exemptions 
            WHERE is_active = true 
            AND (resource_pattern IS NULL OR %s LIKE resource_pattern)
        """
        params = [resource]
        
        if exemption_type:
            query += " AND exemption_type = %s"
            params.append(exemption_type)
        
        result = await write_service.execute_query(query, params)
        return result[0]["count"] > 0
    
    except Exception as e:
        logger.error(f"Error checking exemption for resource {resource}: {e}")
        return False


# Export router for FastAPI app integration
__all__ = ["router", "ExemptionResponse", "ExemptionCreate", "ExemptionUpdate"]