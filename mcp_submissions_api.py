# mcp_submissions_api.py

from datetime import datetime
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import create_engine, Column, String, DateTime, func
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# --- Database Setup ---
# For demonstration, we're using a file-based SQLite database.
# In a production environment, this would typically be a PostgreSQL or similar
# and the database configuration would be externalized (e.g., via environment variables).

# Using a file-based SQLite database for persistence across runs
SQLALCHEMY_DATABASE_URL = "sqlite:///./mcp_submissions.db"

# Create the SQLAlchemy engine
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}  # Required for SQLite
)

# Create a configured "Session" class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for declarative models
Base = declarative_base()

# Dependency to get a database session
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

# --- SQLAlchemy Database Model ---
class McpSubmissionModel(Base):
    """
    SQLAlchemy model for MCP Submissions.
    Stores UUIDs as strings for compatibility with SQLite.
    """
    __tablename__ = "mcp_submissions"

    id = Column(String, primary_key=True, default=lambda: str(uuid4()))
    title = Column(String, index=True, nullable=False)
    description = Column(String, nullable=True)
    status = Column(String, default="pending", nullable=False) # e.g., "pending", "approved", "rejected"
    submitter_id = Column(String, nullable=False) # ID of the user/entity submitting
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)

    def __repr__(self):
        return f"<McpSubmission(id='{self.id}', title='{self.title}')>"

# Create database tables if they don't exist
Base.metadata.create_all(bind=engine)

# --- Pydantic Models for API ---

class McpSubmissionBase(BaseModel):
    """Base model for MCP submission data."""
    title: str = Field(..., min_length=1, max_length=255, description="Title of the MCP submission.")
    description: Optional[str] = Field(None, max_length=1000, description="Detailed description of the submission.")
    status: str = Field("pending", pattern="^(pending|approved|rejected)$", description="Current status of the submission.")
    submitter_id: str = Field(..., min_length=1, max_length=255, description="ID of the user or entity submitting.")

class McpSubmissionCreate(McpSubmissionBase):
    """Model for creating a new MCP submission."""
    pass # Inherits all fields from McpSubmissionBase

class McpSubmissionUpdate(McpSubmissionBase):
    """Model for updating an existing MCP submission."""
    # All fields are optional for updates
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=1000)
    status: Optional[str] = Field(None, pattern="^(pending|approved|rejected)$")
    submitter_id: Optional[str] = Field(None, min_length=1, max_length=255)

class McpSubmissionInDB(McpSubmissionBase):
    """Model for MCP submission data as stored in the database, including generated fields."""
    id: UUID = Field(..., description="Unique identifier of the submission.")
    created_at: datetime = Field(..., description="Timestamp when the submission was created.")
    updated_at: datetime = Field(..., description="Timestamp when the submission was last updated.")

    model_config = ConfigDict(from_attributes=True) # Enable ORM mode for Pydantic v2

# --- FastAPI Router ---

router = APIRouter(
    prefix="/submissions",
    tags=["mcp_submissions"],
    responses={404: {"description": "Submission not found"}},
)

@router.post(
    "/",
    response_model=McpSubmissionInDB,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new MCP submission",
)
async def create_mcp_submission(
    submission: McpSubmissionCreate, db: Session = Depends(get_db)
):
    """
    Creates a new MCP submission in the database with the provided details.
    """
    db_submission = McpSubmissionModel(**submission.model_dump())
    db.add(db_submission)
    db.commit()
    db.refresh(db_submission)
    return db_submission

@router.get(
    "/",
    response_model=List[McpSubmissionInDB],
    summary="Retrieve all MCP submissions",
)
async def get_all_mcp_submissions(
    skip: int = 0, limit: int = 100, db: Session = Depends(get_db)
):
    """
    Retrieves a list of all MCP submissions, with optional pagination.
    """
    submissions = db.query(McpSubmissionModel).offset(skip).limit(limit).all()
    return submissions

@router.get(
    "/{submission_id}",
    response_model=McpSubmissionInDB,
    summary="Retrieve a single MCP submission by ID",
)
async def get_mcp_submission(submission_id: UUID, db: Session = Depends(get_db)):
    """
    Retrieves a specific MCP submission by its unique ID.
    Raises a 404 error if the submission is not found.
    """
    # Convert UUID to string for database query as it's stored as String
    db_submission = db.query(McpSubmissionModel).filter(McpSubmissionModel.id == str(submission_id)).first()
    if db_submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
        )
    return db_submission

@router.put(
    "/{submission_id}",
    response_model=McpSubmissionInDB,
    summary="Update an existing MCP submission",
)
async def update_mcp_submission(
    submission_id: UUID, submission: McpSubmissionUpdate, db: Session = Depends(get_db)
):
    """
    Updates an existing MCP submission identified by its ID.
    Only the fields provided in the request body will be updated.
    """
    db_submission = db.query(McpSubmissionModel).filter(McpSubmissionModel.id == str(submission_id)).first()
    if db_submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
        )

    # Update only the fields that are explicitly set in the request
    update_data = submission.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_submission, key, value)

    db.add(db_submission) # Re-add to session to ensure onupdate hook for 'updated_at' fires
    db.commit()
    db.refresh(db_submission)
    return db_submission

@router.delete(
    "/{submission_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an MCP submission",
)
async def delete_mcp_submission(submission_id: UUID, db: Session = Depends(get_db)):
    """
    Deletes an MCP submission identified by its ID.
    Returns a 204 No Content status on successful deletion.
    """
    db_submission = db.query(McpSubmissionModel).filter(McpSubmissionModel.id == str(submission_id)).first()
    if db_submission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Submission not found"
        )

    db.delete(db_submission)
    db.commit()
    # No content to return for 204 status
    return

# --- Example of how to integrate and run this API (for testing) ---
# In a real application, this would typically be in your main.py or app.py file.

if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI

    app = FastAPI(
        title="MCP Submissions API",
        description="API for managing MCP (Mission Critical Protocol) submissions, including CRUD operations.",
        version="1.0.0",
    )
    app.include_router(router)

    print("Starting FastAPI application...")
    print("Access the API documentation at http://127.0.0.1:8000/docs")
    print("\nTo run this example:")
    print("1. Save the code as `mcp_submissions_api.py`")
    print("2. Install dependencies: `pip install fastapi uvicorn 'sqlalchemy<2' pydantic`")
    print("3. Execute: `python mcp_submissions_api.py`")
    print("\nAcceptance Criteria Check:")
    print(" - GET http://127.0.0.1:8000/submissions should return an empty list initially.")
    print(" - POST http://127.0.0.1:8000/submissions to create a submission.")
    print(" - Subsequent GET requests will validate creation.")

    uvicorn.run(app, host="127.0.0.1", port=8000)