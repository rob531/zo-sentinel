from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import RegistryTrustLandscapeResponse, get_registry_trust_landscape

router = APIRouter(prefix="/api")


@router.get(
    "/registry/trust-landscape",
    response_model=RegistryTrustLandscapeResponse,
    name="registry_trust_landscape",
)
def registry_trust_landscape(
    session: Session = Depends(get_session),
) -> RegistryTrustLandscapeResponse:
    """Return the trust‑landscape view for each registry source."""
    return get_registry_trust_landscape(session)


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    # ----------------------------------------------------------------------- #
    # Create an in‑memory SQLite database and override the session dependency
    # ----------------------------------------------------------------------- #
    engine = create_engine("sqlite:///:memory:", echo=False, future=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def _override_get_session() -> Session:
        return SessionLocal()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override_get_session

    client = TestClient(app)

    # ----------------------------------------------------------------------- #
    # Perform a request against the endpoint
    # ----------------------------------------------------------------------- #
    response = client.get("/api/registry/trust-landscape")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"

    payload = response.json()
    assert isinstance(payload, dict), "Response payload is not a dict"
    assert "sources" in payload, "'sources' key missing in response"
    assert isinstance(payload["sources"], list), "'sources' is not a list"
    assert "generated_at" in payload, "'generated_at' key missing in response"

    # Basic sanity checks on the first source (if any)
    if payload["sources"]:
        src = payload["sources"][0]
        required_keys = {
            "registry_source",
            "server_count",
            "avg_risk_score",
            "tier_distribution",
            "signal_coverage_pct",
        }
        missing = required_keys - src.keys()
        assert not missing, f"Missing keys in source entry: {missing}"
        assert isinstance(src["avg_risk_score"], float), "avg_risk_score is not a float"
        assert isinstance(src["tier_distribution"], dict), "tier_distribution is not a dict"

    # Ensure the generated_at timestamp is parseable
    try:
        datetime.fromisoformat(payload["generated_at"])
    except Exception as exc:
        raise AssertionError(f"generated_at is not ISO‑8601: {exc}")

    print("PASS")