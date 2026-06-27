# quarantined_files_api.py

from fastapi import FastAPI
from typing import List

app = FastAPI()

# In a real system, this would be dynamically read from the system's
# quality gate state (e.g., a database, a configuration file, or an external service).
# For this self-test, we'll use a hardcoded list.
_CURRENTLY_QUARANTINED_FILES: List[str] = [
    "retention_sweeper.py",
    "data_ingestor_v1.py",
    "legacy_report_generator.py",
    "unstable_feature_module.py",
]

@app.get(
    "/quarantined_files",
    response_model=List[str],
    summary="Get list of currently quarantined files",
    description="Reads the list of currently quarantined files from the system's quality gate state.",
)
async def get_quarantined_files() -> List[str]:
    """
    Returns a JSON array of strings, where each string is the filename
    of a module currently held in quarantine by the quality gate.
    """
    return _CURRENTLY_QUARANTINED_FILES

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    print("Running self-test for quarantined_files_api.py...")

    client = TestClient(app)

    # Test GET /quarantined_files
    response = client.get("/quarantined_files")

    # Assert status code is 200 OK
    assert response.status_code == 200, \
        f"FAIL: Expected status code 200, but got {response.status_code}"

    # Assert response is a JSON list
    quarantined_files = response.json()
    assert isinstance(quarantined_files, list), \
        f"FAIL: Expected response to be a list, but got {type(quarantined_files)}"

    # Assert the list is not empty
    assert len(quarantined_files) > 0, \
        "FAIL: Expected a non-empty list of quarantined files"

    # Assert a known quarantined file is present
    known_quarantined_file = "retention_sweeper.py"
    assert known_quarantined_file in quarantined_files, \
        f"FAIL: Expected '{known_quarantined_file}' to be in the list, but it was not."

    print("PASS")

    # Example of how to run the FastAPI app normally (for development/production)
    # import uvicorn
    # print("\nTo run the FastAPI application, use:")
    # print("uvicorn quarantined_files_api:app --reload")
    # print("Then navigate to http://127.0.0.1:8000/quarantined_files")