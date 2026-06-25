from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from typing import Dict, Any
import psycopg2
from psycopg2 import sql

app = FastAPI()

# Database connection parameters
DB_NAME = "your_database_name"
DB_USER = "your_database_user"
DB_PASSWORD = "your_database_password"
DB_HOST = "your_database_host"
DB_PORT = "your_database_port"

def get_db_connection():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )

def get_daemon_statuses() -> Dict[str, str]:
    query = sql.SQL("SELECT daemon_name, status FROM service_health")
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            return dict(cursor.fetchall())

def get_table_row_counts() -> Dict[str, int]:
    tables = ["mcp_server_registry", "mcp_signal_scores", "mcp_threat_associations"]
    row_counts = {}
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            for table in tables:
                query = sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
                cursor.execute(query)
                row_counts[table] = cursor.fetchone()[0]
    return row_counts

def determine_overall_status(daemon_statuses: Dict[str, str]) -> str:
    if all(status == "running" for status in daemon_statuses.values()):
        return "healthy"
    elif any(status == "running" for status in daemon_statuses.values()):
        return "degraded"
    else:
        return "unhealthy"

@app.get("/pipeline_health/summary")
def get_pipeline_health_summary() -> Dict[str, Any]:
    daemon_statuses = get_daemon_statuses()
    table_row_counts = get_table_row_counts()
    overall_status = determine_overall_status(daemon_statuses)

    return {
        "overall_status": overall_status,
        "daemon_statuses": daemon_statuses,
        "table_row_counts": table_row_counts
    }

if __name__ == "__main__":
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/pipeline_health/summary")
    assert response.status_code == 200
    data = response.json()
    assert "overall_status" in data
    assert "daemon_statuses" in data
    assert "table_row_counts" in data
    assert len(data["daemon_statuses"]) >= 3
    assert len(data["table_row_counts"]) >= 3

    print("All tests passed!")