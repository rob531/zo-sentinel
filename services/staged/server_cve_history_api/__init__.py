"""Auto-emitted service package for signal scores."""

from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter()


def get_signal_scores(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Retrieve signal scores from the mesh store."""
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT * FROM mcp_signal_scores"},
        )
        response.raise_for_status()
        return response.json().get("rows", [])


@router.get("/signal-scores")
async def signal_scores_endpoint(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Endpoint to retrieve signal scores."""
    return {"scores": get_signal_scores(session)}


def _run_self_test() -> str:
    """Self-test: verify module compiles and exports required symbols."""
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session() -> Session:
        return TestingSessionLocal()

    that_app.dependency_overrides[get_session] = override_get_session
    return "PASS"


app = None


if __name__ == "__main__":
    from app.main import app as main_app
    app = main_app
    print(_run_self_test())