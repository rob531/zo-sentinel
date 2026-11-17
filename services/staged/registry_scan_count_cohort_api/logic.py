from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _cohort_expression():
    return case(
        (McpServerRegistry.scan_count == 0, "never"),
        (McpServerRegistry.scan_count == 1, "once"),
        (McpServerRegistry.scan_count <= 3, "few"),
        (McpServerRegistry.scan_count <= 10, "many"),
        else_="heavy",
    )


def get_scan_count_cohorts(
    session: Session = Depends(get_session),
) -> dict:
    cohort = _cohort_expression()

    cohort_rows = session.execute(
        select(
            cohort.label("cohort"),
            func.count().label("servers"),
            func.count(McpServerRegistry.registry_source.distinct()).label("sources"),
        ).group_by(cohort)
    ).mappings()

    cohort_order = {"never": 0, "once": 1, "few": 2, "many": 3, "heavy": 4}
    cohorts = [
        {
            "cohort": str(row["cohort"]),
            "servers": int(row["servers"]),
            "sources": int(row["sources"]),
        }
        for row in cohort_rows
    ]
    cohorts.sort(key=lambda row: cohort_order.get(row["cohort"], 99))

    source_rows = session.execute(
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            cohort.label("cohort"),
            func.count().label("servers"),
        ).group_by(McpServerRegistry.registry_source, cohort)
    ).mappings()

    by_source = [
        {
            "registry_source": (
                None
                if row["registry_source"] is None
                else str(row["registry_source"])
            ),
            "cohort": str(row["cohort"]),
            "servers": int(row["servers"]),
        }
        for row in source_rows
    ]
    by_source.sort(
        key=lambda row: (
            row["registry_source"] is None,
            row["registry_source"] or "",
            cohort_order.get(row["cohort"], 99),
        )
    )

    return {
        "total_servers": sum(row["servers"] for row in cohorts),
        "cohorts": cohorts,
        "by_source": by_source,
    }


get_registry_scan_count_cohorts = get_scan_count_cohorts
registry_scan_count_cohorts = get_scan_count_cohorts


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="cohort-test-0",
                    name="Never scanned",
                    registry_source="alpha",
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="cohort-test-1",
                    name="Scanned once",
                    registry_source="alpha",
                    scan_count=1,
                ),
                McpServerRegistry(
                    server_id="cohort-test-2",
                    name="Scanned twice",
                    registry_source="alpha",
                    scan_count=2,
                ),
                McpServerRegistry(
                    server_id="cohort-test-3",
                    name="Scanned seven times",
                    registry_source="beta",
                    scan_count=7,
                ),
                McpServerRegistry(
                    server_id="cohort-test-4",
                    name="Scanned forty times",
                    registry_source="beta",
                    scan_count=40,
                ),
            ]
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/scan-count-cohorts")
    def scan_count_cohorts_endpoint(
        response: dict = Depends(get_scan_count_cohorts),
    ) -> dict:
        return response

    with TestClient(test_app) as client:
        response = client.get("/api/registry/scan-count-cohorts")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 5
        cohort_counts = {
            item["cohort"]: item["servers"] for item in payload["cohorts"]
        }
        assert cohort_counts["never"] == 1
        assert cohort_counts["heavy"] == 1
        assert len(payload["by_source"]) >= 2

    print("PASS")