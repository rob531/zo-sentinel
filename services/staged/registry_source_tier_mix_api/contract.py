from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry
from services.staged.registry_source_tier_mix_api.router import router


def _api_prefix() -> str:
    for route in router.routes:
        path = getattr(route, "path", "")
        if path == "/api" or path.startswith("/api/"):
            return ""
    return "/api"


@contextmanager
def _test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def run_contract_test() -> bool:
    with _test_session() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="registry-mix-1",
                    registry_source="registry-alpha",
                    risk_tier="critical",
                ),
                McpServerRegistry(
                    server_id="registry-mix-2",
                    registry_source="registry-alpha",
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="registry-mix-3",
                    registry_source="registry-beta",
                    risk_tier="high",
                ),
            ]
        )
        session.commit()

        test_app = FastAPI()
        test_app.include_router(router, prefix=_api_prefix())

        def override_get_session():
            yield session

        test_app.dependency_overrides[get_session] = override_get_session

        with TestClient(test_app) as client:
            response = client.get("/api/registry/source-tier-mix")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert set(payload) == {"sources", "totals"}
            assert len(payload["sources"]) == 2

            sources = {
                source["registry_source"]: {
                    "total": source["total"],
                    "tiers": source["tiers"],
                }
                for source in payload["sources"]
            }
            assert sources == {
                "registry-alpha": {
                    "total": 2,
                    "tiers": {"critical": 1, "high": 1},
                },
                "registry-beta": {
                    "total": 1,
                    "tiers": {"high": 1},
                },
            }
            assert payload["totals"] == {"critical": 1, "high": 2}
            assert sum(payload["totals"].values()) == 3

    print("PASS")
    return True


def run_self_test() -> bool:
    return run_contract_test()


def _self_test() -> bool:
    return run_contract_test()


def main() -> bool:
    return run_contract_test()


if __name__ == "__main__":
    main()