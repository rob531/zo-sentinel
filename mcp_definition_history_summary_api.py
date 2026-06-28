import collections
from datetime import date, datetime
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.testclient import TestClient

# --- 1. Simulate mcp_definition_history table ---
# In a real application, this would be a database query.
# We're using an in-memory list of dictionaries for demonstration
# and to adhere to the "No network calls" constraint.
_mcp_definition_history_db: List[Dict[str, Any]] = [
    {
        "mcp_id": "mcp_001",
        "change_type": "CREATE",
        "change_date": date(2023, 1, 10),
        "description": "Initial creation",
    },
    {
        "mcp_id": "mcp_001",
        "change_type": "UPDATE",
        "change_date": date(2023, 2, 15),
        "description": "Updated parameters",
    },
    {
        "mcp_id": "mcp_001",
        "change_type": "UPDATE",
        "change_date": date(2023, 3, 20),
        "description": "Minor fix",
    },
    {
        "mcp_id": "mcp_001",
        "change_type": "DEPLOY",
        "change_date": date(2023, 4, 5),
        "description": "Deployed to production",
    },
    {
        "mcp_id": "mcp_002",
        "change_type": "CREATE",
        "change_date": date(2023, 1, 5),
        "description": "New MCP created",
    },
    {
        "mcp_id": "mcp_002",
        "change_type": "UPDATE",
        "change_date": date(2023, 1, 20),
        "description": "Configured settings",
    },
    {
        "mcp_id": "mcp_003",
        "change_type": "CREATE",
        "change_date": date(2023, 5, 1),
        "description": "Another MCP",
    },
]


# --- 2. Pydantic Model for Response ---
class McpDefinitionHistorySummary(BaseModel):
    mcp_id: str
    total_changes: int
    last_change_date: date
    most_frequent_change_type: str


# --- 3. FastAPI Application ---
app = FastAPI(
    title="MCP Definition History Summary API",
    description="Provides aggregated summary of MCP definition history.",
    version="1.0.0",
)


@app.get(
    "/mcp/{mcp_id}/definition_history/summary",
    response_model=McpDefinitionHistorySummary,
    summary="Get MCP Definition History Summary",
    description="Retrieves an aggregated summary of definition changes for a specific MCP.",
    response_description="Summary of MCP definition history including total changes, last change date, and most frequent change type.",
)
async def get_mcp_definition_history_summary(mcp_id: str):
    """
    Provides an aggregated summary of MCP definition history for a given `mcp_id`.

    - **mcp_id**: The unique identifier of the MCP.
    """
    # Filter history for the given mcp_id
    filtered_history = [
        item for item in _mcp_definition_history_db if item["mcp_id"] == mcp_id
    ]

    if not filtered_history:
        raise HTTPException(
            status_code=404, detail=f"MCP definition history not found for mcp_id: {mcp_id}"
        )

    total_changes = len(filtered_history)

    # Find the last change date
    last_change_date = max(item["change_date"] for item in filtered_history)

    # Determine the most frequent change type
    change_types = [item["change_type"] for item in filtered_history]
    change_type_counts = collections.Counter(change_types)
    most_frequent_change_type = change_type_counts.most_common(1)[0][0]

    return McpDefinitionHistorySummary(
        mcp_id=mcp_id,
        total_changes=total_changes,
        last_change_date=last_change_date,
        most_frequent_change_type=most_frequent_change_type,
    )


# --- 4. Acceptance Tests (using TestClient) ---
if __name__ == "__main__":
    client = TestClient(app)

    print("--- Running Acceptance Tests ---")

    # Test Case 1: Valid mcp_id with existing history (mcp_001)
    known_mcp_id_1 = "mcp_001"
    print(f"\nTesting with known mcp_id: {known_mcp_id_1}")
    response_1 = client.get(f"/mcp/{known_mcp_id_1}/definition_history/summary")

    assert response_1.status_code == 200, f"Expected status 200, got {response_1.status_code}"
    data_1 = response_1.json()

    print(f"Response for {known_mcp_id_1}: {data_1}")

    assert data_1["mcp_id"] == known_mcp_id_1
    assert data_1["total_changes"] == 4
    assert data_1["last_change_date"] == "2023-04-05"
    assert data_1["most_frequent_change_type"] == "UPDATE"
    print(f"Test for {known_mcp_id_1} PASSED.")

    # Test Case 2: Valid mcp_id with existing history (mcp_002)
    known_mcp_id_2 = "mcp_002"
    print(f"\nTesting with known mcp_id: {known_mcp_id_2}")
    response_2 = client.get(f"/mcp/{known_mcp_id_2}/definition_history/summary")

    assert response_2.status_code == 200, f"Expected status 200, got {response_2.status_code}"
    data_2 = response_2.json()

    print(f"Response for {known_mcp_id_2}: {data_2}")

    assert data_2["mcp_id"] == known_mcp_id_2
    assert data_2["total_changes"] == 2
    assert data_2["last_change_date"] == "2023-01-20"
    assert data_2["most_frequent_change_type"] == "CREATE" # Or UPDATE, depends on tie-breaking. Counter.most_common picks first if tie.
    print(f"Test for {known_mcp_id_2} PASSED.")

    # Test Case 3: Valid mcp_id with existing history (mcp_003 - single entry)
    known_mcp_id_3 = "mcp_003"
    print(f"\nTesting with known mcp_id: {known_mcp_id_3}")
    response_3 = client.get(f"/mcp/{known_mcp_id_3}/definition_history/summary")

    assert response_3.status_code == 200, f"Expected status 200, got {response_3.status_code}"
    data_3 = response_3.json()

    print(f"Response for {known_mcp_id_3}: {data_3}")

    assert data_3["mcp_id"] == known_mcp_id_3
    assert data_3["total_changes"] == 1
    assert data_3["last_change_date"] == "2023-05-01"
    assert data_3["most_frequent_change_type"] == "CREATE"
    print(f"Test for {known_mcp_id_3} PASSED.")

    # Test Case 4: Non-existent mcp_id
    non_existent_mcp_id = "mcp_999"
    print(f"\nTesting with non-existent mcp_id: {non_existent_mcp_id}")
    response_4 = client.get(f"/mcp/{non_existent_mcp_id}/definition_history/summary")

    assert response_4.status_code == 404, f"Expected status 404, got {response_4.status_code}"
    assert "detail" in response_4.json()
    assert (
        response_4.json()["detail"]
        == f"MCP definition history not found for mcp_id: {non_existent_mcp_id}"
    )
    print(f"Test for non-existent mcp_id {non_existent_mcp_id} PASSED.")

    print("\nAll Acceptance Tests PASSED!")

    # Optional: Run the app with uvicorn for manual testing
    # To run: uvicorn mcp_definition_history_summary_api:app --reload
    # Then open http://127.0.0.1:8000/docs
    # import uvicorn
    # uvicorn.run(app, host="127.0.0.1", port=8000)