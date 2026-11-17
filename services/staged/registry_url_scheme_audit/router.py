"""HTTP surface for the registry URL scheme audit service."""
from __future__ import annotations

from datetime import datetime
from inspect import Parameter, signature
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

try:
    from .logic import registry_url_scheme_audit as _audit_impl
except ImportError:
    try:
        from .logic import audit_registry_url_schemes as _audit_impl
    except ImportError:
        try:
            from .logic import get_registry_url_scheme_audit as _audit_impl
        except ImportError:
            try:
                from .logic import get_url_scheme_audit as _audit_impl
            except ImportError:
                from .logic import audit_url_schemes as _audit_impl

router = APIRouter(
    prefix="/api/registry/url-scheme-audit",
    tags=["registry"],
)


class NonHttpsExample(BaseModel):
    server_id: str | None = None
    name: str | None = None
    url: str | None = None


class RegistryUrlSchemeAuditResponse(BaseModel):
    total: int
    by_scheme: dict[str, int]
    non_https_examples: list[NonHttpsExample]
    insecure_count: int
    scanned_at: str | datetime | None = None


def _run_audit(db: Session, limit: int) -> Any:
    parameters = signature(_audit_impl).parameters
    if "limit" in parameters:
        parameter = parameters["limit"]
        if parameter.kind is not Parameter.POSITIONAL_ONLY:
            return _audit_impl(db, limit=limit)
    return _audit_impl(db, limit)


@router.get("", response_model=RegistryUrlSchemeAuditResponse)
def get_registry_url_scheme_audit(
    limit: int = Query(default=100, ge=1, le=10_000),
    db: Session = Depends(get_session),
) -> RegistryUrlSchemeAuditResponse:
    payload = _run_audit(db, limit)
    if not isinstance(payload, dict):
        payload = dict(payload)
    return RegistryUrlSchemeAuditResponse(**payload)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    rows = [
        McpServerRegistry(server_id="https-server", name="HTTPS", url="https://example.test"),
        McpServerRegistry(server_id="http-server", name="HTTP", url="http://example.test"),
        McpServerRegistry(server_id="wss-server", name="WSS", url="wss://example.test"),
        McpServerRegistry(server_id="ws-server", name="WS", url="ws://example.test"),
        McpServerRegistry(server_id="none-server", name="None", url=None),
    ]
    with testing_session() as db:
        db.add_all(rows)
        db.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get(
        "/api/registry/url-scheme-audit",
        params={"limit": 5},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total"] == 5, payload
    assert sum(payload["by_scheme"].values()) == 5, payload
    assert payload["insecure_count"] == 2, payload
    print("PASS")


if __name__ == "__main__":
    _self_test()