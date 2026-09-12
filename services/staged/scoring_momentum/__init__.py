from fastapi import FastAPI
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

def get_dependency_overrides():
    return {
        get_session: lambda: Session(bind=None, autocommit=False, autoflush=False)
    }

def main():
    app = FastAPI()
    app.dependency_overrides.update(get_dependency_overrides())

    # Self-test: verify basic imports and session creation
    try:
        session = next(iter(get_dependency_overrides().values()))()
        session.close()
        print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")

if __name__ == "__main__":
    main()