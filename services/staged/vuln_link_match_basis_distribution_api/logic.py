from collections import defaultdict
from typing import Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import VulnLink


async def get_match_basis_distribution(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    result = await session.execute(
        select(
            VulnLink.advisory_id,
            VulnLink.server_id,
            VulnLink.match_basis,
            VulnLink.match_confidence,
        )
    )
    rows = result.all()

    grouped_rows: dict[str, list[tuple[Any, float | None]]] = defaultdict(list)
    advisory_ids: set[Any] = set()
    server_ids: set[Any] = set()

    for advisory_id, server_id, match_basis, match_confidence in rows:
        basis = str(match_basis)
        confidence = (
            float(match_confidence) if match_confidence is not None else None
        )
        grouped_rows[basis].append((server_id, confidence))

        if advisory_id is not None:
            advisory_ids.add(advisory_id)
        if server_id is not None:
            server_ids.add(server_id)

    by_match_basis: list[dict[str, Any]] = []

    for match_basis in sorted(grouped_rows):
        entries = grouped_rows[match_basis]
        confidences = [
            confidence
            for _, confidence in entries
            if confidence is not None
        ]
        distinct_servers = {
            server_id
            for server_id, _ in entries
            if server_id is not None
        }

        by_match_basis.append(
            {
                "match_basis": match_basis,
                "count": len(entries),
                "distinct_servers": len(distinct_servers),
                "min_confidence": min(confidences) if confidences else None,
                "mean_confidence": (
                    sum(confidences) / len(confidences)
                    if confidences
                    else None
                ),
                "max_confidence": max(confidences) if confidences else None,
            }
        )

    return {
        "total_links": len(rows),
        "distinct_advisories": len(advisory_ids),
        "distinct_servers": len(server_ids),
        "by_match_basis": by_match_basis,
    }


if __name__ == "__main__":
    import asyncio

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def prepare_database() -> None:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    """
                    CREATE TABLE vuln_links (
                        id INTEGER PRIMARY KEY,
                        advisory_id INTEGER,
                        server_id INTEGER,
                        match_basis VARCHAR,
                        match_value VARCHAR,
                        match_confidence FLOAT,
                        linked_at DATETIME
                    )
                    """
                )
            )
            await connection.execute(
                text(
                    """
                    INSERT INTO vuln_links
                        (id, advisory_id, server_id, match_basis, match_value,
                         match_confidence)
                    VALUES
                        (1, 10, 100, 'exact_version', 'pkg-1', 0.2),
                        (2, 11, 101, 'exact_version', 'pkg-2', 0.5),
                        (3, 12, 102, 'exact_version', 'pkg-3', 0.8),
                        (4, 13, 103, 'heuristic', 'pkg-4', 0.5),
                        (5, 14, 104, 'heuristic', 'pkg-5', 0.9)
                    """
                )
            )

    asyncio.run(prepare_database())

    test_app = FastAPI()

    @test_app.get("/api/vuln-links/match-basis")
    async def match_basis_endpoint(
        distribution: dict[str, Any] = Depends(get_match_basis_distribution),
    ) -> dict[str, Any]:
        return distribution

    async def override_session():
        async with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    with TestClient(test_app) as client:
        response = client.get("/api/vuln-links/match-basis")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_links"] == 5
        assert len(payload["by_match_basis"]) == 2
        exact_version = next(
            item
            for item in payload["by_match_basis"]
            if item["match_basis"] == "exact_version"
        )
        assert exact_version["count"] == 3
        assert exact_version["mean_confidence"] == 0.5

    asyncio.run(engine.dispose())
    print("PASS")