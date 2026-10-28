from collections import defaultdict
from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def get_adapter_sha_rollup(
    session: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    rollup_rows = session.execute(
        text(
            """
            SELECT
                adapter_sha256,
                COUNT(DISTINCT server_id) AS servers,
                COUNT(*) AS row_count
            FROM mcp_llm_axis_scores
            GROUP BY adapter_sha256
            ORDER BY COUNT(*) DESC
            """
        )
    ).mappings().all()

    version_rows = session.execute(
        text(
            """
            SELECT
                adapter_sha256,
                model_version
            FROM mcp_llm_axis_scores
            WHERE model_version IS NOT NULL
            GROUP BY adapter_sha256, model_version
            ORDER BY adapter_sha256, model_version
            """
        )
    ).mappings().all()

    versions_by_adapter: defaultdict[str, list[str]] = defaultdict(list)
    for row in version_rows:
        adapter_sha256 = row["adapter_sha256"] or "unknown"
        versions_by_adapter[str(adapter_sha256)].append(str(row["model_version"]))

    adapters: list[dict[str, Any]] = []
    for row in rollup_rows:
        adapter_sha256 = row["adapter_sha256"] or "unknown"
        adapters.append(
            {
                "adapter_sha256": str(adapter_sha256),
                "rows": int(row["row_count"]),
                "servers": int(row["servers"]),
                "model_versions": versions_by_adapter.get(str(adapter_sha256), []),
            }
        )

    return adapters


get_adapter_sha_rollups = get_adapter_sha_rollup
list_adapter_sha_rollups = get_adapter_sha_rollup


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as OrmSession, sessionmaker
    from sqlalchemy.pool import StaticPool

    that_app = FastAPI()
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id VARCHAR,
                    axis_name VARCHAR,
                    p_top FLOAT,
                    adapter_sha256 VARCHAR,
                    model_version VARCHAR,
                    scored_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (server_id, axis_name, p_top, adapter_sha256, model_version)
                VALUES
                    ('server-1', 'quality', 0.91, 'adapter-a', 'model-v1'),
                    ('server-2', 'quality', 0.88, 'adapter-a', 'model-v2'),
                    ('server-1', 'safety', 0.93, 'adapter-a', 'model-v1'),
                    ('server-2', 'safety', 0.85, 'adapter-a', 'model-v2'),
                    ('server-1', 'quality', 0.72, 'adapter-b', 'model-v1'),
                    ('server-3', 'safety', 0.69, 'adapter-b', 'model-v1'),
                    ('server-3', 'quality', 0.67, 'adapter-b', 'model-v1'),
                    ('server-3', 'quality', 0.64, NULL, 'model-v3'),
                    ('server-4', 'safety', 0.61, NULL, 'model-v3')
                """
            )
        )

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with testing_session() as session:
            yield session

    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/api/scores/adapter-sha/rollup")
    def adapter_sha_rollup_endpoint(session: OrmSession = Depends(get_session)):
        return {"adapters": get_adapter_sha_rollup(session)}

    response = TestClient(that_app).get("/api/scores/adapter-sha/rollup")
    assert response.status_code == 200
    adapters = response.json()["adapters"]
    assert [adapter["rows"] for adapter in adapters] == [4, 3, 2]
    assert adapters[0]["adapter_sha256"] == "adapter-a"
    assert adapters[0]["servers"] == 2
    assert adapters[0]["model_versions"] == ["model-v1", "model-v2"]
    assert adapters[-1]["adapter_sha256"] == "unknown"
    assert adapters[-1]["servers"] == 2
    print("PASS")