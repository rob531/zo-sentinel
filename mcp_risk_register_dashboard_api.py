# mcp_risk_register_dashboard_api.py

from fastapi import FastAPI, Depends, HTTPException, status
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Date
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship, Session, selectinload
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime, date
import os

# --- Database Configuration ---
# In a real application, this would be loaded from environment variables.
# Example: DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@host:5432/dbname")
# For demonstration, using a placeholder. Replace with your actual database connection string.
DATABASE_URL = "postgresql://user:password@localhost:5432/zo_sentinel_db"

# Create the SQLAlchemy engine
engine = create_engine(DATABASE_URL)

# Configure a local sessionmaker
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Declare the base for declarative models
Base = declarative_base()

# --- SQLAlchemy Models ---
# These models represent the database tables.

class User(Base):
    """Represents the 'users' table, typically for risk owners."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=True) # Can be null if only username is available

    risks_owned = relationship("McpRiskRegister", back_populates="owner")

class McpRiskCategory(Base):
    """Represents the 'mcp_risk_category' table."""
    __tablename__ = "mcp_risk_category"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)

    risks = relationship("McpRiskRegister", back_populates="category")

class McpRiskStatus(Base):
    """Represents the 'mcp_risk_status' table."""
    __tablename__ = "mcp_risk_status"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True, nullable=False)

    risks = relationship("McpRiskRegister", back_populates="status")

class McpRiskRegister(Base):
    """Represents the main 'mcp_risk_register' table."""
    __tablename__ = "mcp_risk_register"
    id = Column(Integer, primary_key=True, index=True)
    risk_name = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    category_id = Column(Integer, ForeignKey("mcp_risk_category.id"), nullable=True)
    status_id = Column(Integer, ForeignKey("mcp_risk_status.id"), nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    impact = Column(Integer, nullable=True) # e.g., 1-5 scale
    likelihood = Column(Integer, nullable=True) # e.g., 1-5 scale
    mitigation_plan = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    due_date = Column(Date, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)

    # Define relationships to related tables
    category = relationship("McpRiskCategory", back_populates="risks", lazy="joined")
    status = relationship("McpRiskStatus", back_populates="risks", lazy="joined")
    owner = relationship("User", back_populates="risks_owned", lazy="joined")

# --- Pydantic Schemas ---
# These schemas define the data structure for API requests and responses.

class RiskCategorySchema(BaseModel):
    """Pydantic schema for risk category data."""
    id: int
    name: str

    class Config:
        orm_mode = True # Enable ORM mode for automatic mapping from SQLAlchemy models

class RiskStatusSchema(BaseModel):
    """Pydantic schema for risk status data."""
    id: int
    name: str

    class Config:
        orm_mode = True

class UserSchema(BaseModel):
    """Pydantic schema for user (owner) data."""
    id: int
    username: str
    full_name: Optional[str] = None # Optional as it might be null in DB

    class Config:
        orm_mode = True

class McpRiskRegisterDashboardItem(BaseModel):
    """
    Pydantic schema for a single risk item as it appears on the dashboard.
    Includes calculated fields and handles potential nulls gracefully.
    """
    id: int
    risk_name: str
    description: Optional[str] = None
    category: Optional[RiskCategorySchema] = None # Nested schema, Optional if FK is nullable
    status: Optional[RiskStatusSchema] = None     # Nested schema, Optional if FK is nullable
    owner: Optional[UserSchema] = None            # Nested schema, Optional if FK is nullable
    impact: Optional[int] = None
    likelihood: Optional[int] = None
    risk_score: Optional[int] = Field(
        None, description="Calculated as Impact * Likelihood. Null if either is missing."
    )
    mitigation_plan: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    due_date: Optional[date] = None
    is_active: bool

    class Config:
        orm_mode = True

# --- FastAPI Application ---
app = FastAPI(
    title="MCP Risk Register Dashboard API",
    description="API to serve data for the MCP Risk Register dashboard, querying risks and related information.",
    version="1.0.0",
)

# Dependency to get the database session
def get_db():
    """
    Provides a database session for each request.
    Ensures the session is closed after the request is processed.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get(
    "/mcp/risk-register/dashboard",
    response_model=List[McpRiskRegisterDashboardItem],
    summary="Retrieve all risks for the MCP Risk Register Dashboard",
    description="""
    Fetches a list of risks from the `mcp_risk_register` table,
    including related category, status, and owner information.
    
    - **Calculates `risk_score`**: `impact * likelihood`. If either `impact` or `likelihood` is null, `risk_score` will be null.
    - **Handles data gaps gracefully**: Related fields (category, status, owner) are `Optional` and will be `null` if the foreign key is not set or the related record doesn't exist.
    - **Filtering**: Supports filtering by `is_active` status.
    """,
    tags=["MCP Risk Register"]
)
async def get_mcp_risk_register_dashboard_data(
    db: Session = Depends(get_db),
    active_only: Optional[bool] = Field(
        True, description="If true, only active risks (is_active=True) are returned."
    )
) -> List[McpRiskRegisterDashboardItem]:
    """
    Endpoint to retrieve formatted risk data for the MCP Risk Register dashboard.
    """
    try:
        # Build the query, eagerly loading relationships to avoid N+1 queries
        query = db.query(McpRiskRegister).options(
            selectinload(McpRiskRegister.category),
            selectinload(McpRiskRegister.status),
            selectinload(McpRiskRegister.owner)
        )

        if active_only:
            query = query.filter(McpRiskRegister.is_active == True)

        risks = query.all()

        dashboard_items = []
        for risk in risks:
            # Calculate risk_score, handling potential None values for impact/likelihood
            risk_score = None
            if risk.impact is not None and risk.likelihood is not None:
                risk_score = risk.impact * risk.likelihood

            # Create a Pydantic model instance, leveraging ORM mode for direct mapping
            # and explicitly setting the calculated risk_score.
            dashboard_items.append(
                McpRiskRegisterDashboardItem.from_orm(risk, update={"risk_score": risk_score})
            )
        
        return dashboard_items
    except Exception as e:
        # Log the exception for debugging purposes (in a real app, use a proper logger)
        print(f"ERROR: Failed to fetch MCP Risk Register dashboard data: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while retrieving risk data."
        )

# --- Database Initialization (for development/testing) ---
# Uncomment the line below to create tables if they don't exist.
# In a production environment, use Alembic or similar migration tools.
# Base.metadata.create_all(bind=engine)

# To run this API:
# 1. Save the code as `mcp_risk_register_dashboard_api.py`.
# 2. Install necessary libraries: `pip install fastapi uvicorn sqlalchemy psycopg2-binary pydantic`
# 3. Ensure your PostgreSQL database is running and accessible with the `DATABASE_URL` provided.
# 4. Run the API using Uvicorn: `uvicorn mcp_risk_register_dashboard_api:app --reload`
# 5. Access the API documentation at `http://127.0.0.1:8000/docs` or `http://127.0.0.1:8000/redoc`.