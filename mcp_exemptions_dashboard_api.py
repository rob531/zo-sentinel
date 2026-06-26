# mcp_exemptions_dashboard_api.py

from typing import List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

# --- IMPORTANT: Placeholder for Database Models and Dependencies ---
# In a real application, these would be imported from your project's
# database setup, e.g.:
# from app.database.models import MCPExemption
# from app.database.dependencies import get_db
#
# For demonstration purposes, we define them here.
# ------------------------------------------------------------------

# Placeholder SQLAlchemy setup for demonstration
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

Base = declarative_base()

class MCPExemption(Base):
    """
    SQLAlchemy model for the 'mcp_exemptions' table.
    Represents an exemption record in the database.
    """
    __tablename__ = "mcp_exemptions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    exemption_id = Column(String, unique=True, index=True, nullable=False,
                          comment="A unique business identifier for the exemption.")
    system_name = Column(String, index=True, nullable=False,
                         comment="Name of the system to which the exemption applies.")
    component_name = Column(String, index=True, nullable=False,
                            comment="Name of the component within the system.")
    exemption_reason = Column(String, nullable=False,
                              comment="Detailed reason for the exemption.")
    start_date = Column(DateTime, nullable=False,
                        comment="Date and time when the exemption period begins.")
    end_date = Column(DateTime, nullable=False,
                      comment="Date and time when the exemption period ends.")
    is_active = Column(Boolean, default=True, nullable=False,
                       comment="Boolean indicating if the exemption is currently active.")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False,
                        comment="Timestamp when the record was created.")
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False,
                        comment="Timestamp when the record was last updated.")

    def __repr__(self):
        return (f"<MCPExemption(id={self.id}, exemption_id='{self.exemption_id}', "
                f"system='{self.system_name}', component='{self.component_name}')>")

# Placeholder for database connection and session management
# Using an in-memory SQLite database for demonstration.
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_mcp_exemptions.db"
# For a purely in-memory database that resets on each run, use:
# SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create tables in the database (for placeholder only)
Base.metadata.create_all(bind=engine)

def get_db():
    """
    Dependency to get a SQLAlchemy database session.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- End Placeholder Section ---


# Pydantic Models for API Request/Response Validation and Serialization
class MCPExemptionBase(BaseModel):
    """
    Base Pydantic model for MCP Exemption data, containing common fields.
    """
    exemption_id: str = Field(..., description="Unique identifier for the exemption (e.g., 'EXM-2023-001').")
    system_name: str = Field(..., description="Name of the system (e.g., 'PaymentGateway').")
    component_name: str = Field(..., description="Name of the component (e.g., 'FraudDetectionService').")
    exemption_reason: str = Field(..., description="Detailed reason for the exemption (e.g., 'Temporary bypass for critical patch deployment').")
    start_date: datetime = Field(..., description="Date and time when the exemption starts (ISO 8601 format).")
    end_date: datetime = Field(..., description="Date and time when the exemption ends (ISO 8601 format).")
    is_active: bool = Field(True, description="Indicates if the exemption is currently active.")

class MCPExemptionInDB(MCPExemptionBase):
    """
    Pydantic model representing an MCP Exemption record as stored in the database,
    including database-generated fields like 'id', 'created_at', 'updated_at'.
    """
    id: int = Field(..., description="Internal database ID of the exemption record.")
    created_at: datetime = Field(..., description="Timestamp when the exemption record was created.")
    updated_at: datetime = Field(..., description="Timestamp when the exemption record was last updated.")

    class Config:
        orm_mode = True  # Enable ORM mode for Pydantic to read directly from SQLAlchemy models

class MCPExemptionResponse(MCPExemptionInDB):
    """
    Pydantic model for the MCP Exemption data returned by the API.
    Inherits from MCPExemptionInDB, can be extended for specific API response needs.
    """
    pass


# FastAPI Router for MCP Exemptions
router = APIRouter(
    prefix="/mcp-exemptions",
    tags=["MCP Exemptions Dashboard"],
    responses={404: {"description": "Not found"}},
)

@router.get(
    "/",
    response_model=List[MCPExemptionResponse],
    summary="Retrieve all MCP exemptions",
    description="""
    Fetches a list of all MCP exemption records from the database.
    Supports pagination and filtering by system name, component name, and active status.
    """,
)
async def get_all_exemptions(
    db: Session = Depends(get_db),
    skip: int = Field(0, ge=0, description="Number of records to skip for pagination."),
    limit: int = Field(100, ge=1, le=1000, description="Maximum number of records to return."),
    system_name: Optional[str] = Field(None, description="Filter by system name (case-insensitive partial match)."),
    component_name: Optional[str] = Field(None, description="Filter by component name (case-insensitive partial match)."),
    is_active: Optional[bool] = Field(None, description="Filter by active status (True for active, False for inactive)."),
):
    """
    Retrieve a list of MCP exemptions with optional filtering and pagination.
    """
    query = db.query(MCPExemption)

    if system_name:
        query = query.filter(MCPExemption.system_name.ilike(f"%{system_name}%"))
    if component_name:
        query = query.filter(MCPExemption.component_name.ilike(f"%{component_name}%"))
    if is_active is not None:
        query = query.filter(MCPExemption.is_active == is_active)

    exemptions = query.offset(skip).limit(limit).all()
    return exemptions

@router.get(
    "/{exemption_id}",
    response_model=MCPExemptionResponse,
    summary="Retrieve a single MCP exemption by its unique business exemption_id",
    description="""
    Fetches a single MCP exemption record using its unique business identifier (`exemption_id`).
    """,
)
async def get_exemption_by_exemption_id(
    exemption_id: str = Field(..., description="The unique business identifier of the exemption to retrieve."),
    db: Session = Depends(get_db),
):
    """
    Retrieve a single MCP exemption by its unique `exemption_id`.
    """
    exemption = db.query(MCPExemption).filter(MCPExemption.exemption_id == exemption_id).first()
    if not exemption:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP Exemption with exemption_id '{exemption_id}' not found."
        )
    return exemption

@router.get(
    "/id/{record_id}",
    response_model=MCPExemptionResponse,
    summary="Retrieve a single MCP exemption by its internal database record ID",
    description="""
    Fetches a single MCP exemption record using its internal database primary key (`id`).
    """,
)
async def get_exemption_by_record_id(
    record_id: int = Field(..., ge=1, description="The internal database ID of the exemption to retrieve."),
    db: Session = Depends(get_db),
):
    """
    Retrieve a single MCP exemption by its internal database `id`.
    """
    exemption = db.query(MCPExemption).filter(MCPExemption.id == record_id).first()
    if not exemption:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP Exemption with record ID '{record_id}' not found."
        )
    return exemption

# --- Example of how this router would be included in a main FastAPI application ---
#
# # In your main.py or app/main.py:
# from fastapi import FastAPI
# from .mcp_exemptions_dashboard_api import router as mcp_exemptions_router
#
# app = FastAPI(
#     title="Zo-Sentinel MCP Exemptions Dashboard API",
#     description="API for managing and querying MCP exemption data for the dashboard.",
#     version="1.0.0",
# )
#
# app.include_router(mcp_exemptions_router)
#
# # To run this example (after installing fastapi, uvicorn, sqlalchemy, pydantic):
# # 1. Save the code above as mcp_exemptions_dashboard_api.py
# # 2. Create a main.py in the same directory with the inclusion logic above.
# # 3. Run: uvicorn main:app --reload
# # 4. Access docs at http://127.0.0.1:8000/docs
#
# # You can also add some dummy data for testing the placeholder:
# @app.on_event("startup")
# async def startup_event():
#     db = SessionLocal()
#     if db.query(MCPExemption).count() == 0:
#         print("Adding dummy data to MCP Exemptions table...")
#         dummy_exemptions = [
#             MCPExemption(
#                 exemption_id="EXM-PG-2023-001",
#                 system_name="PaymentGateway",
#                 component_name="TransactionProcessor",
#                 exemption_reason="Temporary bypass for critical security patch deployment.",
#                 start_date=datetime(2023, 10, 26, 9, 0, 0),
#                 end_date=datetime(2023, 11, 2, 17, 0, 0),
#                 is_active=True
#             ),
#             MCPExemption(
#                 exemption_id="EXM-IDM-2023-002",
#                 system_name="IdentityManagement",
#                 component_name="UserAuthService",
#                 exemption_reason="Planned maintenance window for database upgrade.",
#                 start_date=datetime(2023, 11, 15, 2, 0, 0),
#                 end_date=datetime(2023, 11, 15, 6, 0, 0),
#                 is_active=False
#             ),
#             MCPExemption(
#                 exemption_id="EXM-LOG-2023-003",
#                 system_name="LoggingService",
#                 component_name="LogAggregator",
#                 exemption_reason="High volume event processing, temporary log level adjustment.",
#                 start_date=datetime(2023, 10, 1, 0, 0, 0),
#                 end_date=datetime(2023, 10, 31, 23, 59, 59),
#                 is_active=False
#             ),
#             MCPExemption(
#                 exemption_id="EXM-PG-2024-004",
#                 system_name="PaymentGateway",
#                 component_name="FraudDetectionService",
#                 exemption_reason="A/B testing new fraud detection model.",
#                 start_date=datetime(2024, 1, 1, 0, 0, 0),
#                 end_date=datetime(2024, 3, 31, 23, 59, 59),
#                 is_active=True
#             ),
#         ]
#         db.add_all(dummy_exemptions)
#         db.commit()
#         print(f"Added {len(dummy_exemptions)} dummy exemptions.")
#     db.close()
# ---------------------------------------------------------------------------------