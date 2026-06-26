import datetime
import uuid
from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Session

# --- Pydantic Models ---

class ServerMetadata(BaseModel):
    """
    Pydantic model for comprehensive server metadata.
    """
    server_id: str = Field(..., description="Unique identifier for the server.")
    mcp_name: str = Field(..., description="Name of the MCP (Management Control Plane) managing the server.")
    first_seen: datetime.datetime = Field(..., description="Timestamp when the server was first registered.")
    last_updated: datetime.datetime = Field(..., description="Timestamp when the server's metadata was last updated.")
    # Add other common fields from the registry here.
    # For dynamic fields, consider using a Dict[str, Any] or extra='allow' in a base model.
    # For this task, we'll explicitly list a few common ones and allow others via **kwargs.
    ip_address: Optional[str] = Field(None, description="IP address of the server.")
    os_type: Optional[str] = Field(None, description="Operating system type of the server.")

    class Config:
        from_attributes = True # For SQLAlchemy ORM compatibility

# --- Database Setup (for SQLAlchemy ORM and testing) ---

# Base for declarative models
Base = declarative_base()

class MCPServerRegistry(Base):
    """
    SQLAlchemy model for the mcp_server_registry table.
    """
    __tablename__ = "mcp_server_registry"

    server_id = Column(String, primary_key=True, index=True)
    mcp_name = Column(String, nullable=False)
    first_seen = Column(DateTime, nullable=False)
    last_updated = Column(DateTime, nullable=False)
    ip_address = Column(String, nullable=True)
    os_type = Column(String, nullable=True)
    # Add other columns as they exist in your mcp_server_registry table

    def to_dict(self) -> Dict[str, Any]:
        """Converts the SQLAlchemy model instance to a dictionary."""
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}

# --- FastAPI Router ---

router = APIRouter()

# Dependency to get a database session (will be overridden for testing)
def get_db():
    """
    Placeholder for the actual database session dependency.
    In a real application, this would connect to your primary database.
    """
    raise NotImplementedError("Database dependency not configured for production.")

@router.get("/servers/{server_id}/metadata", response_model=ServerMetadata)
async def get_server_metadata(server_id: str, db: Session = Depends(get_db)):
    """
    Retrieves comprehensive metadata for a specific server.

    Args:
        server_id: The unique identifier of the server.
        db: The database session dependency.

    Returns:
        A JSON object containing the server's metadata.

    Raises:
        HTTPException: If the server with the given ID is not found (404).
    """
    server_record = db.query(MCPServerRegistry).filter(MCPServerRegistry.server_id == server_id).first()

    if not server_record:
        raise HTTPException(status_code=404, detail=f"Server with ID '{server_id}' not found.")

    # Convert SQLAlchemy model to dictionary and then to Pydantic model
    # This handles any extra fields present in the DB but not explicitly in ServerMetadata
    # as long as ServerMetadata is configured with `extra='allow'` or they are Optional.
    # With `from_attributes = True`, Pydantic can directly consume the ORM object.
    return ServerMetadata.model_validate(server_record)

# --- Main block for Acceptance Testing ---

if __name__ == "__main__":
    print("Running acceptance tests...")

    # 1. Setup in-memory SQLite database for testing
    SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db" # Using a file-based SQLite for easier inspection if needed
    # For a truly in-memory, use "sqlite:///:memory:"
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    Base.metadata.create_all(bind=engine)

    # 2. Override the get_db dependency for testing
    def override_get_db():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db

    # 3. Create a TestClient
    client = TestClient(app)

    # 4. Seed the in-memory database with test data
    test_server_id = str(uuid.uuid4())
    now = datetime.datetime.now(datetime.timezone.utc)
    seed_data = MCPServerRegistry(
        server_id=test_server_id,
        mcp_name="TestMCP-001",
        first_seen=now - datetime.timedelta(days=30),
        last_updated=now,
        ip_address="192.168.1.100",
        os_type="Linux"
    )

    with TestingSessionLocal() as db:
        db.add(seed_data)
        db.commit()
        db.refresh(seed_data)
        print(f"Seeded database with server_id: {test_server_id}")

    # 5. Make a GET request and assert
    print(f"Making GET request to /servers/{test_server_id}/metadata")
    response = client.get(f"/servers/{test_server_id}/metadata")

    # Assertions
    assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"
    response_data = response.json()
    assert isinstance(response_data, dict), "Response should be a dictionary"
    assert response_data, "Response dictionary should not be empty"

    expected_keys = ["server_id", "mcp_name", "first_seen", "last_updated", "ip_address", "os_type"]
    for key in expected_keys:
        assert key in response_data, f"Expected key '{key}' not found in response"

    assert response_data["server_id"] == test_server_id, \
        f"Expected server_id '{test_server_id}', got '{response_data['server_id']}'"
    assert response_data["mcp_name"] == "TestMCP-001", \
        f"Expected mcp_name 'TestMCP-001', got '{response_data['mcp_name']}'"
    assert response_data["ip_address"] == "192.168.1.100", \
        f"Expected ip_address '192.168.1.100', got '{response_data['ip_address']}'"

    # Test for a non-existent server
    non_existent_id = str(uuid.uuid4())
    print(f"Making GET request for non-existent server_id: {non_existent_id}")
    response_404 = client.get(f"/servers/{non_existent_id}/metadata")
    assert response_404.status_code == 404, f"Expected status code 404 for non-existent server, got {response_404.status_code}"
    assert "detail" in response_404.json()
    assert f"Server with ID '{non_existent_id}' not found." in response_404.json()["detail"]

    print("PASS")

    # Clean up the test database file if it was created
    import os
    if os.path.exists("./test.db"):
        os.remove("./test.db")
    if os.path.exists("./test.db-journal"): # SQLite might create a journal file
        os.remove("./test.db-journal")