import sqlite3
from typing import List, Dict, Any
from fastapi import FastAPI, APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, Field
from fastapi.testclient import TestClient

# --- 1. Pydantic Model for Policy Rule Schema ---
class PolicyRuleCreate(BaseModel):
    """
    Pydantic model for creating a new policy rule.
    Corresponds to the schema of the mcp_policy_rules table.
    """
    rule_type: str = Field(..., max_length=50, description="Type of the policy rule (e.g., 'regex', 'keyword').")
    pattern: str = Field(..., max_length=255, description="The pattern to match (e.g., a regex string, a keyword). This field is used for idempotency (UNIQUE).")
    description: str = Field(..., max_length=500, description="A human-readable description of the rule.")
    severity: str = Field(..., max_length=20, description="Severity level (e.g., 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW').")
    action: str = Field(..., max_length=50, description="Action to take when the rule matches (e.g., 'BLOCK', 'ALERT', 'LOG').")

    class Config:
        schema_extra = {
            "example": {
                "rule_type": "regex",
                "pattern": r"\b(secret|confidential)\b",
                "description": "Detects common keywords indicating sensitive information.",
                "severity": "HIGH",
                "action": "ALERT"
            }
        }

# --- 2. Database Service ---
# For this example, we'll use an in-memory SQLite database for simplicity and testing.
# In a real application, this would connect to a persistent database (e.g., PostgreSQL, MySQL).

def create_mcp_policy_rules_table(db_conn: sqlite3.Connection):
    """Creates the mcp_policy_rules table if it doesn't exist."""
    cursor = db_conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mcp_policy_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_type TEXT NOT NULL,
            pattern TEXT NOT NULL UNIQUE, -- Pattern must be UNIQUE for INSERT OR REPLACE to work effectively
            description TEXT NOT NULL,
            severity TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    db_conn.commit()

class WriteService:
    """
    Service to handle write operations to the mcp_policy_rules table.
    Uses INSERT OR REPLACE for idempotency based on the 'pattern' field.
    """
    def __init__(self, db_conn: sqlite3.Connection):
        self.db_conn = db_conn

    def insert_or_replace_policy_rule(self, rule_data: PolicyRuleCreate) -> Dict[str, Any]:
        """
        Inserts a new policy rule or replaces an existing one if a rule with the same pattern exists.
        """
        cursor = self.db_conn.cursor()
        try:
            # Using INSERT OR REPLACE INTO to ensure idempotency based on the UNIQUE 'pattern' field.
            cursor.execute("""
                INSERT OR REPLACE INTO mcp_policy_rules 
                (rule_type, pattern, description, severity, action)
                VALUES (?, ?, ?, ?, ?)
            """, (
                rule_data.rule_type,
                rule_data.pattern,
                rule_data.description,
                rule_data.severity,
                rule_data.action
            ))
            self.db_conn.commit()
            return {"message": f"Policy rule with pattern '{rule_data.pattern}' seeded successfully."}
        except sqlite3.Error as e:
            self.db_conn.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database error during seeding: {e}"
            )

# --- 3. FastAPI Application Setup ---
app = FastAPI(
    title="MCP Policy Rules Seeding API",
    description="API to manually seed initial policy rules into the mcp_policy_rules table.",
    version="1.0.0",
)

router = APIRouter()

# --- 4. Dependency Injection ---
def get_db():
    """Dependency to get a database connection."""
    conn = None
    try:
        conn = sqlite3.connect(":memory:") # Use in-memory for testing by default
        conn.row_factory = sqlite3.Row # Optional: to fetch rows as dicts
        create_mcp_policy_rules_table(conn) # Ensure table exists
        yield conn
    finally:
        if conn:
            conn.close()

def get_write_service(db: sqlite3.Connection = Depends(get_db)):
    """Dependency to get the WriteService instance."""
    return WriteService(db)

# --- 5. FastAPI Endpoint ---
@router.post(
    "/seed_policy_rules",
    response_model=Dict[str, Any], # Or a custom success model
    status_code=status.HTTP_200_OK,
    summary="Seed initial policy rules",
    description="Manually seed a list of policy rules into the `mcp_policy_rules` table. "
                "Uses `INSERT OR REPLACE` for idempotency based on the `pattern` field."
)
async def seed_policy_rules(
    rules: List[PolicyRuleCreate],
    write_service: WriteService = Depends(get_write_service)
):
    """
    Accepts a list of policy rule dictionaries and seeds them into the database.
    Each rule is inserted or replaced based on its `pattern` field.
    """
    if not rules:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Request body must contain a non-empty list of policy rules."
        )

    seeded_results = []
    for rule in rules:
        result = write_service.insert_or_replace_policy_rule(rule)
        seeded_results.append(result)

    return {"message": f"Successfully seeded {len(rules)} policy rules.", "details": seeded_results}

app.include_router(router)

# --- 6. __main__ block for testing ---
if __name__ == "__main__":
    # Setup an in-memory SQLite database for testing
    test_db_conn = sqlite3.connect(":memory:")
    test_db_conn.row_factory = sqlite3.Row # Allows accessing columns by name
    create_mcp_policy_rules_table(test_db_conn)

    # Override the get_db dependency to use our test database connection
    def override_get_db():
        try:
            yield test_db_conn
        finally:
            # The connection is managed by the test scope, no need to close here
            pass

    app.dependency_overrides[get_db] = override_get_db

    client = TestClient(app)

    print("--- Running FastAPI TestClient for /seed_policy_rules ---")

    # Test Case 1: Seed two distinct policy rules
    test_rules_payload = [
        {
            "rule_type": "keyword",
            "pattern": "password_leak",
            "description": "Detects potential password leaks.",
            "severity": "CRITICAL",
            "action": "BLOCK"
        },
        {
            "rule_type": "regex",
            "pattern": r"\b(ssn|social security number)\b",
            "description": "Detects Social Security Numbers.",
            "severity": "HIGH",
            "action": "ALERT"
        }
    ]

    response = client.post("/seed_policy_rules", json=test_rules_payload)

    assert response.status_code == status.HTTP_200_OK, f"Test 1 Failed: Expected status 200, got {response.status_code}"
    response_json = response.json()
    assert "Successfully seeded 2 policy rules." in response_json["message"], f"Test 1 Failed: Unexpected message: {response_json['message']}"
    assert len(response_json["details"]) == 2, f"Test 1 Failed: Expected 2 details, got {len(response_json['details'])}"
    print(f"Test Case 1 (Initial Seed) - Response: {response_json}")

    # Verify rules are in the database
    cursor = test_db_conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM mcp_policy_rules")
    count = cursor.fetchone()[0]
    assert count == 2, f"Test 1 Failed: Expected 2 rules in DB, got {count}"

    cursor.execute("SELECT pattern, severity FROM mcp_policy_rules WHERE pattern = 'password_leak'")
    rule1 = cursor.fetchone()
    assert rule1 is not None and rule1['severity'] == 'CRITICAL', "Test 1 Failed: Rule 'password_leak' not found or incorrect"

    cursor.execute("SELECT pattern, severity FROM mcp_policy_rules WHERE pattern = '\\b(ssn|social security number)\\b'")
    rule2 = cursor.fetchone()
    assert rule2 is not None and rule2['severity'] == 'HIGH', "Test 1 Failed: Rule 'ssn' not found or incorrect"

    print("Test Case 1: Successfully seeded two distinct policy rules and verified their presence.")

    # Test Case 2: Seed one new rule and update an existing one (idempotency check)
    update_and_new_rules_payload = [
        {
            "rule_type": "keyword",
            "pattern": "password_leak", # Existing rule, will be replaced
            "description": "Updated: Detects potential password leaks with enhanced logic.",
            "severity": "MEDIUM", # Changed severity
            "action": "LOG" # Changed action
        },
        {
            "rule_type": "url",
            "pattern": "malicious.example.com", # New rule
            "description": "Detects access to known malicious domains.",
            "severity": "CRITICAL",
            "action": "BLOCK"
        }
    ]

    response = client.post("/seed_policy_rules", json=update_and_new_rules_payload)

    assert response.status_code == status.HTTP_200_OK, f"Test 2 Failed: Expected status 200, got {response.status_code}"
    response_json = response.json()
    assert "Successfully seeded 2 policy rules." in response_json["message"], f"Test 2 Failed: Unexpected message: {response_json['message']}"
    print(f"Test Case 2 (Update and New Seed) - Response: {response_json}")

    # Verify rules in the database after update/new
    cursor.execute("SELECT COUNT(*) FROM mcp_policy_rules")
    count = cursor.fetchone()[0]
    # We had 2 rules. One was updated, one was new. Total should be 3.
    assert count == 3, f"Test 2 Failed: Expected 3 rules in DB (1 updated, 1 new, 1 unchanged), got {count}"

    cursor.execute("SELECT pattern, severity, action, description FROM mcp_policy_rules WHERE pattern = 'password_leak'")
    updated_rule = cursor.fetchone()
    assert updated_rule is not None, "Test 2 Failed: Updated rule 'password_leak' not found"
    assert updated_rule['severity'] == 'MEDIUM', f"Test 2 Failed: Expected severity 'MEDIUM', got {updated_rule['severity']}"
    assert updated_rule['action'] == 'LOG', f"Test 2 Failed: Expected action 'LOG', got {updated_rule['action']}"
    assert "Updated: Detects potential password leaks" in updated_rule['description'], "Test 2 Failed: Description not updated"

    cursor.execute("SELECT pattern, severity FROM mcp_policy_rules WHERE pattern = 'malicious.example.com'")
    new_rule = cursor.fetchone()
    assert new_rule is not None and new_rule['severity'] == 'CRITICAL', "Test 2 Failed: New rule 'malicious.example.com' not found or incorrect"

    print("Test Case 2: Successfully updated an existing rule and added a new one (idempotency verified).")

    # Test Case 3: Empty payload
    response = client.post("/seed_policy_rules", json=[])
    assert response.status_code == status.HTTP_400_BAD_REQUEST, f"Test 3 Failed: Expected status 400 for empty payload, got {response.status_code}"
    assert "non-empty list of policy rules" in response.json()["detail"], f"Test 3 Failed: Unexpected error detail: {response.json()['detail']}"
    print("Test Case 3: Handled empty payload correctly.")

    # Test Case 4: Invalid payload (missing required field)
    invalid_payload = [
        {
            "rule_type": "keyword",
            "pattern": "invalid_rule",
            "description": "Missing severity",
            "action": "ALERT"
            # 'severity' is intentionally missing
        }
    ]
    response = client.post("/seed_policy_rules", json=invalid_payload)
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, f"Test 4 Failed: Expected status 422 for invalid payload, got {response.status_code}"
    assert "field required" in response.json()["detail"][0]["msg"], f"Test 4 Failed: Unexpected error detail: {response.json()['detail']}"
    print("Test Case 4: Handled invalid payload (missing field) correctly.")

    test_db_conn.close() # Close the test database connection

    print("\nAll tests passed. PASS")