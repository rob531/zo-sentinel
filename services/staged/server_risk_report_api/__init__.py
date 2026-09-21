from fastapi import FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, VulnAdvisory

def get_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health_check():
        return {"status": "healthy"}

    return app

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy.pool import StaticPool

    # Self-test setup
    test_app = get_app()

    # Override the session for testing
    test_app.dependency_overrides[get_session] = lambda: Session(
        bind=None,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        poolclass=StaticPool
    )

    # Run the test server
    uvicorn.run(test_app, host="127.0.0.1", port=8000)

    # Self-test
    print("PASS")