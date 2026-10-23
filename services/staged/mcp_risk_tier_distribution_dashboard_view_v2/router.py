from fastapi import APIRouter, Depends
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

from .logic import get_risk_tier_distribution

router = APIRouter(prefix="", tags=["risk-tier-distribution-dashboard-view-v2"])


@router.get("/api/risk-tier-distribution-dashboard/v2")
async def api_risk_tier_distribution_dashboard_v2(
    session=Depends(get_session),
):
    return await get_risk_tier_distribution(session)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base

    Base.metadata.create_all(bind=engine)

    app = FastAPI()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    print("PASS")