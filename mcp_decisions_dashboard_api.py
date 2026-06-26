# app/api/v1/mcp_decisions_dashboard_api.py

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

# --- Assume these imports are available from your project structure ---
# from app.database import get_db
# from app.models import MCPDecision
# from app.schemas import MCPDecisionResponse, PaginatedMCPDecisions
# -------------------------------------------------------------------

# --- Placeholder for database dependency (replace with actual import) ---
# In a real application, this would be in app/database.py
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import func

# Database setup (for demonstration purposes, replace with your actual setup)
SQLALCHEMY_DATABASE_URL = "sqlite:///./sql_app.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
# -------------------------------------------------------------------


# --- Placeholder for SQLAlchemy Model (replace with actual import from app/models.py) ---
# In a real application, this would be in app/models.py
class MCPDecision(Base):
    __tablename__ = "mcp_decisions"

    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(String, index=True, nullable=False)
    decision_type = Column(String, nullable=False) # e.g., "Approval", "Rejection", "Review"
    decision_date = Column(DateTime, nullable=False)
    decision_maker = Column(String, nullable=False)
    status = Column(String, nullable=False) # e.g., "Approved", "Rejected", "Pending", "Closed"
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<MCPDecision(id={self.id}, case_id='{self.case_id}', status='{self.status}')>"

# Create tables (for demonstration, remove in production if using migrations)
Base.metadata.create_all(bind=engine)
# -------------------------------------------------------------------


# --- Placeholder for Pydantic Schemas (replace with actual import from app/schemas.py) ---
# In a real application, these would be in app/schemas.py
from pydantic import BaseModel

class MCPDecisionBase(BaseModel):
    case_id: str
    decision_type: str
    decision_date: datetime
    decision_maker: str
    status: str
    notes: Optional[str] = None

    class Config:
        from_attributes = True # For Pydantic v2, use orm_mode = True for Pydantic v1

class MCPDecisionResponse(MCPDecisionBase):
    id: int
    created_at: datetime
    updated_at: datetime

class PaginatedMCPDecisions(BaseModel):
    total: int
    page: int
    size: int
    items: List[MCPDecisionResponse]
# -------------------------------------------------------------------


router = APIRouter(
    prefix="/mcp-decisions",
    tags=["MCP Decisions Dashboard"],
    responses={404: {"description": "Not found"}},
)

@router.get(
    "/",
    response_model=PaginatedMCPDecisions,
    summary="Retrieve a paginated list of MCP decisions",
    description="Fetches a list of MCP decisions with options for pagination, filtering by case ID, decision type, status, date range, and sorting."
)
def read_mcp_decisions(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1, description="Page number to retrieve"),
    size: int = Query(10, ge=1, le=100, description="Number of items per page"),
    case_id: Optional[str] = Query(None, description="Filter decisions by a partial case ID match (case-insensitive)"),
    decision_type: Optional[str] = Query(None, description="Filter decisions by a partial decision type match (case-insensitive)"),
    status: Optional[str] = Query(None, description="Filter decisions by a partial status match (case-insensitive)"),
    decision_maker: Optional[str] = Query(None, description="Filter decisions by a partial decision maker match (case-insensitive)"),
    start_date: Optional[datetime] = Query(None, description="Filter decisions made on or after this date (YYYY-MM-DDTHH:MM:SS)"),
    end_date: Optional[datetime] = Query(None, description="Filter decisions made on or before this date (YYYY-MM-DDTHH:MM:SS)"),
    sort_by: Optional[str] = Query("decision_date", description="Field to sort by (e.g., id, case_id, decision_date, status)"),
    sort_order: Optional[str] = Query("desc", description="Sort order (asc or desc)"),
):
    """
    Retrieve a list of MCP decisions with advanced filtering and pagination.
    """
    query = db.query(MCPDecision)

    # Apply filters
    if case_id:
        query = query.filter(MCPDecision.case_id.ilike(f"%{case_id}%"))
    if decision_type:
        query = query.filter(MCPDecision.decision_type.ilike(f"%{decision_type}%"))
    if status:
        query = query.filter(MCPDecision.status.ilike(f"%{status}%"))
    if decision_maker:
        query = query.filter(MCPDecision.decision_maker.ilike(f"%{decision_maker}%"))
    if start_date:
        query = query.filter(MCPDecision.decision_date >= start_date)
    if end_date:
        query = query.filter(MCPDecision.decision_date <= end_date)

    total = query.count()

    # Apply sorting
    sort_column = getattr(MCPDecision, sort_by, None)
    if sort_column is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_by field: '{sort_by}'. Allowed fields: id, case_id, decision_type, decision_date, decision_maker, status, created_at, updated_at."
        )

    if sort_order.lower() == "asc":
        query = query.order_by(sort_column.asc())
    elif sort_order.lower() == "desc":
        query = query.order_by(sort_column.desc())
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sort_order. Must be 'asc' or 'desc'."
        )

    # Apply pagination
    decisions = query.offset((page - 1) * size).limit(size).all()

    return PaginatedMCPDecisions(
        total=total,
        page=page,
        size=size,
        items=[MCPDecisionResponse.from_orm(d) for d in decisions]
    )

@router.get(
    "/{decision_id}",
    response_model=MCPDecisionResponse,
    summary="Retrieve a single MCP decision by ID",
    description="Fetches a specific MCP decision using its unique identifier."
)
def read_mcp_decision(
    decision_id: int,
    db: Session = Depends(get_db)
):
    """
    Retrieve a single MCP decision by its ID.
    """
    decision = db.query(MCPDecision).filter(MCPDecision.id == decision_id).first()
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP Decision with ID {decision_id} not found"
        )
    return decision

# --- Example of how to integrate this router into your main FastAPI app (e.g., in app/main.py) ---
# from fastapi import FastAPI
# from app.api.v1.mcp_decisions_dashboard_api import router as mcp_decisions_dashboard_router

# app = FastAPI(title="Zo-Sentinel MCP Decisions Dashboard API")

# app.include_router(mcp_decisions_dashboard_router, prefix="/api/v1")

# # To run this example directly for testing:
# if __name__ == "__main__":
#     import uvicorn
#     # Create some dummy data for testing
#     with SessionLocal() as db:
#         if db.query(MCPDecision).count() == 0:
#             print("Adding dummy data...")
#             db.add_all([
#                 MCPDecision(case_id="CASE001", decision_type="Approval", decision_date=datetime(2023, 1, 15), decision_maker="John Doe", status="Approved", notes="Initial approval for project Alpha."),
#                 MCPDecision(case_id="CASE002", decision_type="Rejection", decision_date=datetime(2023, 2, 20), decision_maker="Jane Smith", status="Rejected", notes="Lack of required documentation."),
#                 MCPDecision(case_id="CASE003", decision_type="Review", decision_date=datetime(2023, 3, 10), decision_maker="Alice Brown", status="Pending", notes="Awaiting further information from applicant."),
#                 MCPDecision(case_id="CASE004", decision_type="Approval", decision_date=datetime(2023, 4, 5), decision_maker="John Doe", status="Approved", notes="Conditional approval."),
#                 MCPDecision(case_id="CASE005", decision_type="Rejection", decision_date=datetime(2023, 5, 1), decision_maker="Jane Smith", status="Rejected", notes="Policy violation."),
#                 MCPDecision(case_id="CASE006", decision_type="Approval", decision_date=datetime(2023, 6, 22), decision_maker="Alice Brown", status="Approved", notes="Final approval for project Beta."),
#                 MCPDecision(case_id="CASE007", decision_type="Review", decision_date=datetime(2023, 7, 1), decision_maker="John Doe", status="Pending", notes="Under internal review."),
#                 MCPDecision(case_id="CASE008", decision_type="Approval", decision_date=datetime(2023, 8, 10), decision_maker="Jane Smith", status="Approved", notes="Expedited approval."),
#                 MCPDecision(case_id="CASE009", decision_type="Rejection", decision_date=datetime(2023, 9, 1), decision_maker="Alice Brown", status="Rejected", notes="Budget constraints."),
#                 MCPDecision(case_id="CASE010", decision_type="Approval", decision_date=datetime(2023, 10, 15), decision_maker="John Doe", status="Approved", notes="Standard approval process."),
#             ])
#             db.commit()
#             print("Dummy data added.")
#     app_for_testing = FastAPI()
#     app_for_testing.include_router(router, prefix="/api/v1")
#     uvicorn.run(app_for_testing, host="0.0.0.0", port=8000)
# -------------------------------------------------------------------------------------------------