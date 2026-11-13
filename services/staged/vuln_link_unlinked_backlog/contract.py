from __future__ import annotations

import sys
import uuid


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpServerRegistry, VulnAdvisory, VulnLink

    from .router import router

    def typed_id(column, label: str):
        python_type = column.type.python_type
        if python_type is uuid.UUID:
            return uuid.uuid5(uuid.NAMESPACE_URL, f"vuln-link-unlinked-backlog/{label}")
        if python_type is int:
            return 1
        if python_type is str:
            return f"test-{label}"
        return python_type()

    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    TestingSession = sessionmaker(bind=eng, autoflush=False, autocommit=False)

    linked_id = typed_id(McpServerRegistry.__table__.c.server_id, "linked")
    unlinked_id_1 = typed_id(McpServerRegistry.__table__.c.server_id, "unlinked-1")
    unlinked_id_2 = typed_id(McpServerRegistry.__table__.c.server_id, "unlinked-2")

    s = TestingSession()
    try:
        s.add_all(
            [
                McpServerRegistry(
                    server_id=linked_id,
                    name="Linked server",
                    registry_source="acceptance",
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id=unlinked_id_1,
                    name="Unlinked server one",
                    registry_source="acceptance",
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id=unlinked_id_2,
                    name="Unlinked server two",
                    registry_source="acceptance",
                    risk_tier="LOW",
                ),
            ]
        )

        advisory = VulnAdvisory(
            id=typed_id(VulnAdvisory.__table__.c.id, "advisory"),
            feed="acceptance",
            source_url="https://example.invalid/advisories/test",
            ecosystem="npm",
            severity="HIGH",
        )
        s.add(advisory)
        s.flush()

        s.add(
            VulnLink(
                id=typed_id(VulnLink.__table__.c.id, "link"),
                advisory_id=advisory.id,
                server_id=linked_id,
                match_basis="server_id",
                match_confidence=1.0,
                match_value=str(linked_id),
            )
        )
        s.commit()
    finally:
        s.close()

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    with TestClient(app) as client:
        response = client.get("/api/vuln/backlog/unlinked")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total_unlinked"] == 2, body
        item_ids = {item["server_id"] for item in body["items"]}
        assert linked_id not in item_ids, body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)