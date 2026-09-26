from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpServerRegistry


class RouteInfo(BaseModel):
    path: str
    methods: list[str]
    service: str


class RouterInfo(BaseModel):
    name: str
    service: str
    routes: list[RouteInfo]


class SpineManifest(BaseModel):
    routers: list[RouterInfo]
    routes: list[RouteInfo]
    timestamp: str


router = APIRouter(tags=["spine_manifest"])


def _service_unit(route: APIRoute) -> str:
    module = getattr(route.endpoint, "__module__", "")
    parts = module.split(".")
    if module == "__main__":
        return (route.tags or [route.name])[0]
    if len(parts) > 2 and parts[0] == "services" and parts[1] in {"active", "staged"}:
        return parts[2]
    if parts[0] == "app":
        return parts[1] if len(parts) > 1 and parts[1] != "main" else "app"
    return parts[-1] if parts and parts[-1] else route.name


def get_spine_manifest(app: FastAPI | None = None) -> SpineManifest:
    if app is None:
        from app.main import app as main_app

        app = main_app

    flat_routes: list[RouteInfo] = []
    grouped: dict[tuple[str, str], list[RouteInfo]] = {}
    for mounted_route in app.routes:
        if not isinstance(mounted_route, APIRoute):
            continue
        service = _service_unit(mounted_route)
        route_info = RouteInfo(
            path=mounted_route.path,
            methods=sorted(mounted_route.methods or []),
            service=service,
        )
        flat_routes.append(route_info)
        router_name = (mounted_route.tags or [service])[0]
        grouped.setdefault((router_name, service), []).append(route_info)

    routers = [
        RouterInfo(name=name, service=service, routes=routes)
        for (name, service), routes in sorted(grouped.items())
    ]
    return SpineManifest(
        routers=routers,
        routes=flat_routes,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


def get_server_by_id(server_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    server = session.get(McpServerRegistry, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return _serialize_server(server)


def get_all_servers(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    servers = session.query(McpServerRegistry).order_by(McpServerRegistry.server_id).all()
    return [_serialize_server(server) for server in servers]


def _serialize_server(server: McpServerRegistry) -> dict[str, Any]:
    fields = (
        "server_id",
        "name",
        "registry_source",
        "url",
        "description",
        "trust_score",
        "verdict",
        "verdict_reasoning",
        "confidence",
        "first_seen",
        "last_seen",
        "last_scanned",
        "scan_count",
        "risk_tier",
    )
    result: dict[str, Any] = {}
    for field in fields:
        value = getattr(server, field)
        result[field] = value.isoformat() if isinstance(value, datetime) else value
    return result


@router.get("/spine_manifest", response_model=SpineManifest)
def spine_manifest(request: Request, session: Session = Depends(get_session)) -> SpineManifest:
    return get_spine_manifest(request.app)


def run() -> bool:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with TestingSession() as db:
        db.add(
            McpServerRegistry(
                server_id="fixture-server",
                name="Fixture Server",
                description="Contract-test record",
                registry_source="contract-test",
                risk_tier="low",
            )
        )
        db.commit()
        assert get_server_by_id("fixture-server", db)["name"] == "Fixture Server"
        assert [item["server_id"] for item in get_all_servers(db)] == ["fixture-server"]

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    fixture_router = APIRouter(tags=["contract_fixture"])

    def fixture_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    fixture_endpoint.__module__ = "services.active.contract_fixture.router"
    fixture_router.add_api_route("/fixture", fixture_endpoint, methods=["GET"])
    app.include_router(fixture_router)
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/spine_manifest")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["routes"], body
        assert any(
            item["path"] == "/fixture"
            and item["methods"] == ["GET"]
            and item["service"] == "contract_fixture"
            for item in body["routes"]
        ), body
        assert any(
            item["name"] == "contract_fixture"
            and item["service"] == "contract_fixture"
            and any(route["path"] == "/fixture" for route in item["routes"])
            for item in body["routers"]
        ), body
        assert any(
            route["path"] == "/spine_manifest" and route["service"] == "spine_manifest"
            for route in body["routes"]
        ), body
        assert client.get("/fixture").json() == {"status": "ok"}
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)
