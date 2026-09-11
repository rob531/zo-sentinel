from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import (
    Org,
    User,
    McpServerRegistry,
    McpLlmAxisScore,
    McpScoreDispute,
)

app = FastAPI(title="Auto Emitted Service Package")


@app.get("/health")
def health():
    return {"status": "ok"}


__all__ = [
    "app",
    "get_session",
    "Org",
    "User",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
]


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # Override DB dependency with a no‑op stub for self‑test
    app.dependency_overrides[get_session] = lambda: None

    client = TestClient(app)
    response = client.get("/health")
    if response.status_code == 200 and response.json().get("status") == "ok":
        print("PASS")
    else:
        print("FAIL")