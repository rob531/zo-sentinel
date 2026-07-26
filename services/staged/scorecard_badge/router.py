# services/staged/scorecard_badge/router.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import compute_scorecard_badge, ScorecardBadgeResponse

router = APIRouter(prefix="/api")


@router.get(
    "/servers/{server_id}/scorecard",
    response_model=ScorecardBadgeResponse,
    name="get_scorecard_badge",
)
def get_scorecard(
    server_id: int,
    db: Session = Depends(get_session),
) -> ScorecardBadgeResponse:
    """
    Retrieve the compact trust badge for a given server.
    """
    try:
        return compute_scorecard_badge(db, server_id)
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=404, detail=str(exc))


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base
    from app.models import McpServerRegistry, McpLlmAxisScore

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup
    # ------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    # ------------------------------------------------------------------- #
    # Dependency override
    # ------------------------------------------------------------------- #
    def _override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # ------------------------------------------------------------------- #
    # Seed test data
    # ------------------------------------------------------------------- #
    def _seed():
        with TestingSessionLocal() as db:
            # Server 1 – TRUSTED (all axes >=70, p_critical <=0.3)
            db.add(McpServerRegistry(server_id=1))
            for axis in [
                "confidentiality",
                "integrity",
                "availability",
                "authenticity",
                "non_repudiation",
                "privacy",
                "resilience",
            ]:
                db.add(
                    McpLlmAxisScore(
                        server_id=1,
                        axis=axis,
                        score=80.0,
                        p_top=0.1,
                        p_critical=0.1,
                    )
                )

            # Server 2 – CAUTION (one axis <50 or p_critical >0.3)
            db.add(McpServerRegistry(server_id=2))
            for axis in [
                "confidentiality",
                "integrity",
                "availability",
                "authenticity",
                "non_repudiation",
                "privacy",
                "resilience",
            ]:
                score = 45.0 if axis == "integrity" else 75.0
                p_critical = 0.4 if axis == "integrity" else 0.1
                db.add(
                    McpLlmAxisScore(
                        server_id=2,
                        axis=axis,
                        score=score,
                        p_top=0.2,
                        p_critical=p_critical,
                    )
                )

            # Server 3 – INSUFFICIENT (only 3 axes scored)
            db.add(McpServerRegistry(server_id=3))
            for axis in ["confidentiality", "integrity", "availability"]:
                db.add(
                    McpLlmAxisScore(
                        server_id=3,
                        axis=axis,
                        score=60.0,
                        p_top=0.2,
                        p_critical=0.1,
                    )
                )
            db.commit()

    _seed()

    # ------------------------------------------------------------------- #
    # FastAPI app wiring
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.dependency_overrides[get_session] = _override_get_session
    app.include_router(router)

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Test cases
    # ------------------------------------------------------------------- #
    expectations = {
        1: "TRUSTED",
        2: "CAUTION",
        3: "INSUFFICIENT",
    }

    all_ok = True
    for srv_id, expected_badge in expectations.items():
        resp = client.get(f"/api/servers/{srv_id}/scorecard")
        if resp.status_code != 200:
            print(f"❌ Server {srv_id}: unexpected status {resp.status_code}", file=sys.stderr)
            all_ok = False
            continue
        data = resp.json()
        badge = data.get("badge")
        if badge != expected_badge:
            print(
                f"❌ Server {srv_id}: badge {badge!r} != expected {expected_badge!r}",
                file=sys.stderr,
            )
            all_ok = False

    if all_ok:
        print("PASS")
    else:
        sys.exit(1)