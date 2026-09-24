from fastapi import APIRouter, Depends, FastAPI
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute

router = APIRouter()


@router.get("/health")
def health():
    return {"status": "ok"}


# Example endpoint showing DB session usage without referencing unknown columns
@router.get("/servers")
def list_servers(session=Depends(get_session)):
    return session.query(McpServerRegistry).limit(10).all()


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    client = TestClient(app)
    response = client.get("/health")
    if response.status_code == 200 and response.json().get("status") == "ok":
        print("PASS")
    else:
        print("FAIL")