import asyncio
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

# --- Mock Populator Daemon Function ---
# In a real scenario, this would be a function exposed by or a message queue
# interface to the mcp_definition_history_populator_daemon.py
# For this exercise, we simulate its behavior.

async def _mock_trigger_history_population(
    mcp_id: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
):
    """
    Simulates the non-blocking trigger of the history population daemon.
    In a real application, this might send a message to a queue (e.g., RabbitMQ, Kafka)
    or call an internal function of the daemon if it's running in the same process
    (though typically daemons are separate processes).
    """
    print(f"[{datetime.now()}] Triggering MCP Definition History Population:")
    if mcp_id:
        print(f"  - Specific MCP ID: {mcp_id}")
    if start_date and end_date:
        print(f"  - Date Range: {start_date.isoformat()} to {end_date.isoformat()}")
    elif start_date:
        print(f"  - Start Date: {start_date.isoformat()} (no end date specified)")
    elif end_date:
        print(f"  - End Date: {end_date.isoformat()} (no start date specified)")
    if not mcp_id and not start_date and not end_date:
        print("  - Full history backfill/re-run requested.")

    # Simulate some asynchronous work or message sending
    await asyncio.sleep(0.1) # Small delay to simulate async operation
    print(f"[{datetime.now()}] Trigger signal sent for history population.")


# --- FastAPI Application ---

router = APIRouter()

class TriggerRequest(BaseModel):
    """
    Request model for triggering the MCP definition history population.
    """
    mcp_id: Optional[str] = Field(
        None,
        description="Optional: Specific MCP ID to trigger history population for.",
        example="mcp_12345"
    )
    start_date: Optional[date] = Field(
        None,
        description="Optional: Start date (ISO 8601 format, e.g., '2023-01-01') for a partial backfill.",
        example="2023-01-01"
    )
    end_date: Optional[date] = Field(
        None,
        description="Optional: End date (ISO 8601 format, e.g., '2023-01-31') for a partial backfill.",
        example="2023-01-31"
    )

@router.post(
    "/api/v1/mcp_definition_history/trigger_population",
    summary="Manually trigger MCP Definition History Population",
    response_description="Status of the trigger request"
)
async def trigger_mcp_definition_history_population(
    request: TriggerRequest,
    background_tasks: BackgroundTasks
):
    """
    Triggers the `mcp_definition_history_populator_daemon` to run.

    This endpoint can initiate a full history backfill or a partial one
    based on the provided `mcp_id` or a `start_date`/`end_date` range.

    - If no parameters are provided, a full history population is triggered.
    - If `mcp_id` is provided, history for that specific MCP is populated.
    - If `start_date` and/or `end_date` are provided, history for that date range is populated.
      - `start_date` must be less than or equal to `end_date` if both are provided.
    """
    mcp_id = request.mcp_id
    start_date = request.start_date
    end_date = request.end_date

    # Input validation for date range
    if start_date and end_date and start_date > end_date:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date cannot be after end_date."
        )

    # Add the actual trigger function to background tasks for non-blocking execution
    background_tasks.add_task(
        _mock_trigger_history_population,
        mcp_id=mcp_id,
        start_date=start_date,
        end_date=end_date
    )

    message = "MCP definition history population triggered successfully."
    if mcp_id:
        message += f" For MCP ID: {mcp_id}."
    if start_date and end_date:
        message += f" For date range: {start_date.isoformat()} to {end_date.isoformat()}."
    elif start_date:
        message += f" Starting from date: {start_date.isoformat()}."
    elif end_date:
        message += f" Up to date: {end_date.isoformat()}."
    elif not mcp_id and not start_date and not end_date:
        message += " Full backfill initiated."

    return {"status": "success", "message": message}

app = FastAPI(
    title="MCP Definition History Trigger API",
    description="API to manually trigger the MCP Definition History Populator Daemon.",
    version="1.0.0"
)
app.include_router(router)


# --- Acceptance Tests (using TestClient) ---
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock, patch

    client = TestClient(app)

    print("Running acceptance tests...")

    # Patch the actual populator function with an AsyncMock for verification
    with patch(
        "mcp_definition_history_manual_trigger_api._mock_trigger_history_population",
        new_callable=AsyncMock
    ) as mock_populator:
        test_cases = [
            {
                "name": "No parameters (full backfill)",
                "payload": {},
                "expected_status": 200,
                "expected_message_part": "Full backfill initiated.",
                "expected_populator_args": {"mcp_id": None, "start_date": None, "end_date": None}
            },
            {
                "name": "With mcp_id",
                "payload": {"mcp_id": "test_mcp_001"},
                "expected_status": 200,
                "expected_message_part": "For MCP ID: test_mcp_001.",
                "expected_populator_args": {"mcp_id": "test_mcp_001", "start_date": None, "end_date": None}
            },
            {
                "name": "With start_date and end_date",
                "payload": {"start_date": "2023-01-01", "end_date": "2023-01-31"},
                "expected_status": 200,
                "expected_message_part": "For date range: 2023-01-01 to 2023-01-31.",
                "expected_populator_args": {"mcp_id": None, "start_date": date(2023, 1, 1), "end_date": date(2023, 1, 31)}
            },
            {
                "name": "With mcp_id and date range",
                "payload": {"mcp_id": "test_mcp_002", "start_date": "2023-02-01", "end_date": "2023-02-28"},
                "expected_status": 200,
                "expected_message_part": "For MCP ID: test_mcp_002. For date range: 2023-02-01 to 2023-02-28.",
                "expected_populator_args": {"mcp_id": "test_mcp_002", "start_date": date(2023, 2, 1), "end_date": date(2023, 2, 28)}
            },
            {
                "name": "With only start_date",
                "payload": {"start_date": "2023-03-15"},
                "expected_status": 200,
                "expected_message_part": "Starting from date: 2023-03-15.",
                "expected_populator_args": {"mcp_id": None, "start_date": date(2023, 3, 15), "end_date": None}
            },
            {
                "name": "With only end_date",
                "payload": {"end_date": "2023-04-20"},
                "expected_status": 200,
                "expected_message_part": "Up to date: 2023-04-20.",
                "expected_populator_args": {"mcp_id": None, "start_date": None, "end_date": date(2023, 4, 20)}
            },
            {
                "name": "Invalid date format for start_date",
                "payload": {"start_date": "2023/01/01"},
                "expected_status": 422, # Unprocessable Entity from Pydantic
                "expected_message_part": "value is not a valid date",
                "expected_populator_args": None # Should not be called
            },
            {
                "name": "Invalid date range (start_date > end_date)",
                "payload": {"start_date": "2023-02-01", "end_date": "2023-01-01"},
                "expected_status": 400, # Bad Request from custom validation
                "expected_message_part": "start_date cannot be after end_date.",
                "expected_populator_args": None # Should not be called
            },
        ]

        all_tests_passed = True
        for i, tc in enumerate(test_cases):
            print(f"\n--- Test Case {i+1}: {tc['name']} ---")
            mock_populator.reset_mock() # Clear calls for each test

            response = client.post("/api/v1/mcp_definition_history/trigger_population", json=tc["payload"])

            try:
                assert response.status_code == tc["expected_status"], \
                    f"Expected status {tc['expected_status']}, got {response.status_code} for {tc['name']}"
                assert tc["expected_message_part"] in response.json().get("message", response.json().get("detail", "")), \
                    f"Expected message part '{tc['expected_message_part']}' not found in response for {tc['name']}"

                if tc["expected_populator_args"] is not None:
                    # Check if the mock populator was called
                    mock_populator.assert_called_once()
                    # Verify arguments passed to the mock populator
                    call_args, call_kwargs = mock_populator.call_args
                    assert call_kwargs == tc["expected_populator_args"], \
                        f"Populator called with incorrect args for {tc['name']}. Expected {tc['expected_populator_args']}, got {call_kwargs}"
                else:
                    # For error cases, ensure the populator was NOT called
                    mock_populator.assert_not_called()

                print(f"Test '{tc['name']}' PASSED.")
            except AssertionError as e:
                print(f"Test '{tc['name']}' FAILED: {e}")
                print(f"Response: {response.json()}")
                all_tests_passed = False

        if all_tests_passed:
            print("\nAll acceptance tests PASSED.")
        else:
            print("\nSome acceptance tests FAILED.")