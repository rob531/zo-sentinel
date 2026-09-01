from fastapi import FastAPI, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User

app = FastAPI()

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

def main():
    # Self-test: verify imports and basic functionality
    try:
        # Test database session dependency
        session = Depends(get_session)()
        session.close()

        # Test model imports
        McpServerRegistry()
        McpLlmAxisScore()
        McpScoreDispute()
        Org()
        User()

        print("PASS")
    except Exception as e:
        print(f"FAIL: {str(e)}")

if __name__ == "__main__":
    main()