from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_server_risk_detail

router = APIRouter(prefix="/api")


@router.get("/servers/{server_id}/risk")
async def server_risk_detail(
    server_id: int, session: Session = Depends(get_session)
):
    return await get_server_risk_detail(server_id, session)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base, McpLlmAxisScore

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (overrides the real Postgres dependency)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        echo=False,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    Base.metadata.create_all(bind=engine)

    def get_test_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ------------------------------------------------------------------- #
    # Seed minimal data required by the logic layer
    # ------------------------------------------------------------------- #
    with SessionLocal() as db:
        axes = [
            "confidentiality",
            "integrity",
            "availability",
            "authenticity",
            "nonrepudiation",
            "privacy",
        ]
        for axis in axes:
            db.add(
                McpLlmAxisScore(
                    server_id=1,
                    axis=axis,
                    label="CRITICAL" if axis == "confidentiality" else "LOW",
                    p_top=0.9 if axis == "confidentiality" else 0.1,
                    overall_risk=0.8,
                    risk_tier="CRITICAL" if axis == "confidentiality" else "LOW",
                    criteria_version="v1",
                )
            )
        # overall risk row
        db.add(
            McpLlmAxisScore(
                server_id=1,
                axis="overall",
                label="CRITICAL",
                p_top=0.9,
                overall_risk=0.8,
                risk_tier="CRITICAL",
                criteria_version="v1",
            )
        )
        db.commit()

    # ------------------------------------------------------------------- #
    # Execute test request
    # ------------------------------------------------------------------- #
    client = TestClient(app)
    response = client.get("/api/servers/1/risk")
    if response.status_code != 200:
        sys.exit(f"FAIL – unexpected status {response.status_code}")

    payload = response.json()
    axes = payload.get("axes", {})
    if len(axes) != 7:
        sys.exit(f"FAIL – expected 7 axes, got {len(axes)}")
    if "risk_tier" not in payload:
        sys.exit("FAIL – missing risk_tier in response")

    print("PASS")