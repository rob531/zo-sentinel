from collections.abc import Generator

from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


router = APIRouter(tags=["scoring"])

_SCORES_TABLE = McpLlmAxisScore.__tablename__

_MODEL_VERSION_COVERAGE_SQL = text(
    f"""
    SELECT
        COALESCE(model_version, 'unknown') AS model_version,
        COUNT(*) AS score_count,
        COUNT(DISTINCT server_id) AS server_count,
        COUNT(DISTINCT axis_name) AS axis_count,
        MAX(scored_at) AS last_scored_at
    FROM {_SCORES_TABLE}
    GROUP BY COALESCE(model_version, 'unknown')
    ORDER BY model_version ASC
    """
)


@router.get("/scoring/model-version-coverage")
def get_model_version_coverage(
    session: Session = Depends(get_session),
) -> dict:
    rows = session.execute(_MODEL_VERSION_COVERAGE_SQL).mappings().all()
    model_versions = [
        {
            "model_version": row["model_version"],
            "score_count": int(row["score_count"]),
            "server_count": int(row["server_count"]),
            "axis_count": int(row["axis_count"]),
            "last_scored_at": row["last_scored_at"],
        }
        for row in rows
    ]
    return {
        "model_versions": model_versions,
        "total_model_versions": len(model_versions),
    }


app = FastAPI(title="Scoring Model Version Coverage API")
app.include_router(router, prefix="/api")


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as test_session:
        test_session.execute(
            text(
                f"""
                CREATE TABLE {_SCORES_TABLE} (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    model_version TEXT,
                    scored_at TEXT
                )
                """
            )
        )
        test_session.execute(
            text(
                f"""
                INSERT INTO {_SCORES_TABLE}
                    (id, server_id, axis_name, model_version, scored_at)
                VALUES
                    (1, 'server-1', 'security', 'm1', '2024-01-01T00:00:00Z'),
                    (2, 'server-2', 'quality', 'm1', '2024-01-02T00:00:00Z'),
                    (3, 'server-3', 'security', 'm1', '2024-01-03T00:00:00Z'),
                    (4, 'server-1', 'quality', 'm2', '2024-01-04T00:00:00Z')
                """
            )
        )
        test_session.commit()

    def override_get_session() -> Generator[Session, None, None]:
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/scoring/model-version-coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_model_versions"] == 2
    assert len(payload["model_versions"]) == 2

    by_version = {
        item["model_version"]: item for item in payload["model_versions"]
    }
    assert by_version["m1"]["server_count"] == 3
    assert by_version["m1"]["score_count"] == 3
    assert by_version["m1"]["axis_count"] == 2
    assert by_version["m1"]["last_scored_at"] is not None
    assert by_version["m2"]["server_count"] == 1
    assert by_version["m2"]["last_scored_at"] is not None

    print("PASS")