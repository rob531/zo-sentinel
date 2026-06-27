import asyncio
from fastapi import FastAPI, APIRouter, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# --- Simulated Backfill Trigger Logic ---
async def trigger_backfill_process():
    """
    Simulates sending a signal to initiate the mcp_definition_history backfill.

    In a real-world scenario, this function would delegate the actual work
    to the `mcp_definition_history_backfill_daemon` or an interfacing utility.
    Possible delegation mechanisms include:
    1. Writing a specific entry to a control table that the daemon polls.
    2. Sending a message to a message queue (e.g., RabbitMQ, Kafka) that the daemon consumes.
    3. Directly invoking an importable function from the `mcp_definition_history_backfill_daemon`
       if it's designed to expose such an interface.

    This simulation uses a small asyncio sleep to represent a non-blocking operation
    involved in dispatching the signal.
    """
    print("[SIMULATION] Sending signal to mcp_definition_history_backfill_daemon...")
    # Simulate an asynchronous, non-blocking operation to send the signal
    await asyncio.sleep(0.05) # A small delay to mimic I/O or inter-process communication
    print("[SIMULATION] Signal dispatched successfully.")
    return True # Indicate that the signal was successfully dispatched

# --- FastAPI Application Setup ---
app = FastAPI(
    title="MCP Definition History Backfill Trigger API",
    description="API to manually trigger the mcp_definition_history backfill process."
)

router = APIRouter(prefix="/mcp")

@router.post("/definition_history/backfill/trigger", status_code=status.HTTP_202_ACCEPTED)
async def trigger_mcp_definition_history_backfill():
    """
    Triggers the mcp_definition_history backfill process.

    This endpoint sends a signal to the `mcp_definition_history_backfill_daemon`
    to initiate the backfill from `mcp_submissions`. The actual backfill
    process runs asynchronously and is handled by the daemon, ensuring
    the API remains responsive.
    """
    try:
        # Delegate the actual triggering logic to a utility function.
        # This function should not perform direct DB writes but interface with the daemon.
        signal_sent = await trigger_backfill_process()

        if signal_sent:
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={"message": "MCP definition history backfill trigger signal sent successfully."}
            )
        else:
            # This path might be taken if trigger_backfill_process had internal logic to fail
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to send backfill trigger signal due to an internal issue."
            )
    except Exception as e:
        # Catch any unexpected errors during the signal sending process
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred while triggering backfill: {str(e)}"
        )

# Include the router in the main FastAPI application
app.include_router(router)

# --- Acceptance Test Block ---
if __name__ == "__main__":
    print("--- Running Acceptance Test ---")

    # Initialize TestClient with the FastAPI app
    client = TestClient(app)

    # Make a POST request to the endpoint
    response = client.post("/mcp/definition_history/backfill/trigger")

    # Assertions
    expected_status_code = status.HTTP_202_ACCEPTED
    expected_response_message = {"message": "MCP definition history backfill trigger signal sent successfully."}

    assert response.status_code == expected_status_code, \
        f"FAIL: Expected status code {expected_status_code}, but got {response.status_code}. Response: {response.json()}"
    assert response.json() == expected_response_message, \
        f"FAIL: Expected response {expected_response_message}, but got {response.json()}"

    print("PASS")

    # Optional: Uncomment the following lines to run the server
    # for manual testing with a tool like curl or Postman.
    # import uvicorn
    # print("\n--- Starting Uvicorn server for manual testing (Ctrl+C to stop) ---")
    # print("Access API docs at http://127.0.0.1:8000/docs")
    # uvicorn.run(app, host="0.0.0.0", port=8000)