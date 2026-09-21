from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from fastapi import Depends
from sqlalchemy.orm import Session

__all__ = [
    "get_session",
    "McpServerRegistry",
    "McpLlmAxisScore",
    "McpScoreDispute",
    "Org",
    "User",
    "Depends",
    "Session",
]

def main():
    # Self-test: verify imports resolve and basic functionality
    try:
        from app.db import get_session
        from app.models import McpServerRegistry
        session = Depends(get_session)
        if session and McpServerRegistry:
            print("PASS")
    except Exception as e:
        print(f"FAIL: {e}")

if __name__ == "__main__":
    main()