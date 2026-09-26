from fastapi import APIRouter, Depends, HTTPException
from app.db import get_session
from app.models import McpServerRegistry  # Import the appropriate model
from sqlalchemy.orm import Session
from typing import List, Dict, Any

router = APIRouter(
    prefix="/api",
    tags=["import_row_delta"],
    responses={404: {"description": "Not found"}},
)

@router.post("/import_row_delta")
async def import_row_delta(
    data: List[Dict[str, Any]],
    db: Session = Depends(get_session)
):
    \"\"
    Import row delta data into the database.

    Args:
        data: List of dictionaries containing row delta data.
        db: Database session.

    Returns:
        Dictionary with success message and count of imported rows.
    \"\"
    try:
        # Process and import data
        for row in data:
            # Example: Insert or update data in the McpServerRegistry table
            db.execute(
                McpServerRegistry.__table__.insert().values(**row),
                on_conflict_do_update(
                    index_elements=[McpServerRegistry.server_id],
                    set_=row
                )
            )
        db.commit()
        return {"message": "Row delta imported successfully", "count": len(data)}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error importing row delta: {str(e)}")
