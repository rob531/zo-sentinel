from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from .logic import get_deferred_routers

router = APIRouter(prefix="/api/reports")

@router.get("/deferred-routers")
async def get_deferred_router_report(session: Session = Depends(get_session)):
    return await get_deferred_routers(session)

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import McpServerRegistry
    import datetime

    # Override the session for testing
    test_app = FastAPI()
    test_app.include_router(router)

    # Create a test database
    Base.metadata.create_all(bind=engine)

    # Seed test data
    with engine.connect() as conn:
        conn.execute(
            McpServerRegistry.__table__.insert(),
            [
                {
                    "router_name": "router1",
                    "declared_in": "module1",
                    "last_modified": datetime.datetime.now(),
                    "is_mounted": True,
                },
                {
                    "router_name": "router2",
                    "declared_in": "module2",
                    "last_modified": datetime.datetime.now(),
                    "is_mounted": True,
                },
                {
                    "router_name": "router3",
                    "declared_in": "module3",
                    "last_modified": datetime.datetime.now(),
                    "is_mounted": False,
                },
            ],
        )

    client = TestClient(test_app)
    response = client.get("/api/reports/deferred-routers")
    assert response.status_code == 200
    assert len(response.json()["deferred_routers"]) == 1
    assert response.json()["deferred_routers"][0]["router_name"] == "router3"
    print("PASS")