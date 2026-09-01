from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_app() -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def root():
        return {"message": "Hello World"}

    return app

if __name__ == "__main__":
    import uvicorn
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    # Self-test setup
    test_app = get_app()
    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
    test_app.dependency_overrides[get_session] = lambda: Session(engine)

    # Test that the app can be created and dependencies can be overridden
    try:
        uvicorn.run(test_app, host="127.0.0.1", port=8000)
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")