from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel

from app.db import get_session
from verdict_breakdown_api import get_verdict_breakdown


router = APIRouter(prefix="/verdict", tags=["verdict"])


class VerdictBreakdownResponse(BaseModel):
    axes: list
    overall: float
    risk_tier: str
    criteria_version: str


@router.get("/breakdown/{server_id}", response_model=VerdictBreakdownResponse)
def get_breakdown(
    server_id: str,
    session=Depends(get_session),
):
    result = get_verdict_breakdown(session, server_id)
    return VerdictBreakdownResponse(**result)


def register_routes(app: FastAPI) -> None:
    app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    app = FastAPI()
    register_routes(app)

    engine = create_engine("sqlite:///:memory:", echo=False)
    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/verdict/breakdown/test-server")
    assert response.status_code == 200
    data = response.json()
    assert "axes" in data
    assert "overall" in data
    assert "risk_tier" in data
    assert len(data) > 0
    print("PASS")