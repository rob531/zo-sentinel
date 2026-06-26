from fastapi import FastAPI, APIRouter
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import duckdb
from fastapi.testclient import TestClient

# --- Pydantic Models ---

class HealthResponse(BaseModel):
    is_populated: bool
    last_updated: Optional[datetime] = None

class StatusResponse(BaseModel):
    row_count: int
    oldest_entry_age_days: Optional[float] = None
    newest_entry_age_days: Optional[float] = None
    gap_summary: str

# --- Database Interaction (using DuckDB for testing) ---

def get_db_session():
    # In a real app, this would be a proper DB session (e.g., SQLAlchemy)
    # For testing, we use an in-memory DuckDB
    conn = duckdb.connect(database=':memory:', read_only=False)
    return conn

def seed_db(conn):
    conn.execute("""
        CREATE TABLE mcp_definition_history (
            id INTEGER PRIMARY KEY,
            definition_id VARCHAR,
            version INTEGER,
            timestamp TIMESTAMP
        );
    """)
    now = datetime.now()
    conn.execute("INSERT INTO mcp_definition_history VALUES (1, 'def1', 1, ?)", [now - timedelta(days=5)])
    conn.execute("INSERT INTO mcp_definition_history VALUES (2, 'def1', 2, ?)", [now - timedelta(days=3)])
    conn.execute("INSERT INTO mcp_definition_history VALUES (3, 'def2', 1, ?)", [now - timedelta(days=1)])
    conn.execute("INSERT INTO mcp_definition_history VALUES (4, 'def1', 3, ?)", [now])

# --- API Logic ---

def get_health() -> dict:
    conn = get_db_session()
    try:
        result = conn.execute("SELECT MAX(timestamp) FROM mcp_definition_history").fetchone()
        if result and result[0]:
            last_updated = result[0]
            return HealthResponse(is_populated=True, last_updated=last_updated).dict()
        else:
            return HealthResponse(is_populated=False).dict()
    finally:
        conn.close()

def get_status() -> dict:
    conn = get_db_session()
    try:
        now = datetime.now()

        # Row count
        row_count_result = conn.execute("SELECT COUNT(*) FROM mcp_definition_history").fetchone()
        row_count = row_count_result[0] if row_count_result else 0

        if row_count == 0:
            return StatusResponse(row_count=0, gap_summary="No data available.").dict()

        # Oldest and newest entry timestamps
        timestamps_result = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM mcp_definition_history").fetchone()
        min_ts, max_ts = timestamps_result

        oldest_entry_age_days = (now - min_ts).total_seconds() / (60 * 60 * 24) if min_ts else None
        newest_entry_age_days = (now - max_ts).total_seconds() / (60 * 60 * 24) if max_ts else None

        # Gap detection (simplified: check if definition versions are sequential for each definition_id)
        gap_summary = ""
        definition_versions = conn.execute("""
            SELECT definition_id, version
            FROM mcp_definition_history
            ORDER BY definition_id, timestamp
        """).fetchall()

        gaps_found = False
        current_def = None
        expected_version = 1
        for def_id, version in definition_versions:
            if def_id != current_def:
                current_def = def_id
                expected_version = 1
            if version != expected_version:
                gap_summary += f"Gap detected for '{def_id}': expected version {expected_version}, found {version}. "
                gaps_found = True
            expected_version += 1

        if not gaps_found:
            gap_summary = "No gaps detected in definition history."

        return StatusResponse(
            row_count=row_count,
            oldest_entry_age_days=oldest_entry_age_days,
            newest_entry_age_days=newest_entry_age_days,
            gap_summary=gap_summary.strip()
        ).dict()
    finally:
        conn.close()

# --- FastAPI Router ---

router = APIRouter()

@router.get("/health", response_model=HealthResponse)
async def health_endpoint():
    return get_health()

@router.get("/status", response_model=StatusResponse)
async def status_endpoint():
    return get_status()

# --- Main Block for Testing ---

if __name__ == "__main__":
    from datetime import timedelta

    # Setup FastAPI app and test client
    app = FastAPI()
    app.include_router(router, prefix="/mcp_definition_history")
    client = TestClient(app)

    # Seed the in-memory database
    conn = get_db_session()
    seed_db(conn)
    conn.close()

    # Test /health endpoint
    print("Testing /mcp_definition_history/health...")
    response_health = client.get("/mcp_definition_history/health")
    assert response_health.status_code == 200
    health_data = response_health.json()
    print(f"Health Response: {health_data}")
    assert health_data["is_populated"] is True
    assert health_data["last_updated"] is not None
    print("Health endpoint test PASSED.")

    # Test /status endpoint
    print("\nTesting /mcp_definition_history/status...")
    response_status = client.get("/mcp_definition_history/status")
    assert response_status.status_code == 200
    status_data = response_status.json()
    print(f"Status Response: {status_data}")
    assert status_data["row_count"] == 4
    assert status_data["oldest_entry_age_days"] is not None
    assert status_data["newest_entry_age_days"] is not None
    assert "Gap detected" in status_data["gap_summary"] # Based on seeded data
    print("Status endpoint test PASSED.")

    print("\nAll tests PASSED!")