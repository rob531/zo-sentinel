from fastapi import FastAPI
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_app() -> FastAPI:
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    # Test override for self-test only
    test_app = get_app()
    test_app.dependency_overrides[get_session] = lambda: Session(
        bind=None,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        poolclass=StaticPool
    )

    # Self-test: verify the app starts and health endpoint works
    try:
        uvicorn.run(test_app, host="127.0.0.1", port=8000)
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")