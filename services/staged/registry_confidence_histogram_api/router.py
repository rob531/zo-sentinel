from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_confidence_histogram

router = APIRouter()


@router.get("/registry/confidence-histogram")
def registry_confidence_histogram(
    bins: int = Query(default=10, ge=1, le=100),
    session: Session = Depends(get_session),
):
    return get_confidence_histogram(session, bins)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, confidence, verdict, risk_tier, last_assessed)
                VALUES
                    ('server-1', 0.05, 'TRUSTED_GENERAL', 'LOW', CURRENT_TIMESTAMP),
                    ('server-2', 0.50, 'TRUSTED_GENERAL', 'MEDIUM', CURRENT_TIMESTAMP),
                    ('server-3', 0.95, 'UNTRUSTED', 'HIGH', CURRENT_TIMESTAMP),
                    ('server-4', NULL, 'UNKNOWN', 'UNKNOWN', CURRENT_TIMESTAMP)
                """
            )
        )

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = lambda: test_engine.connect()

    with TestClient(test_app) as client:
        response = client.get("/api/registry/confidence-histogram", params={"bins": 10})
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["buckets"]) == 10
        assert payload["unknown_count"] == 1
        assert any(bucket["count"] == 1 for bucket in payload["buckets"])

    print("PASS")