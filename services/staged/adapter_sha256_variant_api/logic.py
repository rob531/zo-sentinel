from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api/scoring", tags=["scoring"])

ADAPTER_VARIANTS_SQL = """
SELECT
    adapter_sha256,
    model_version,
    COUNT(*) AS row_count,
    COUNT(DISTINCT server_id) AS server_count,
    MIN(scored_at) AS first_seen,
    MAX(scored_at) AS last_seen
FROM mcp_llm_axis_scores
WHERE adapter_sha256 IS NOT NULL
  AND adapter_sha256 <> ''
GROUP BY adapter_sha256, model_version
ORDER BY row_count DESC
"""


class AdapterVariant(BaseModel):
    adapter_sha256: str
    model_version: Optional[str]
    row_count: int
    server_count: int
    first_seen: str
    last_seen: str


class AdapterVariantsResponse(BaseModel):
    variants: List[AdapterVariant]
    total_variants: int
    total_rows: int


def _isoformat(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


@router.get("/adapter-variants", response_model=AdapterVariantsResponse)
def get_adapter_variants(session=Depends(get_session)) -> AdapterVariantsResponse:
    result = session.execute(text(ADAPTER_VARIANTS_SQL))
    variants: List[AdapterVariant] = []
    total_rows = 0

    for row in result.mappings():
        variant = AdapterVariant(
            adapter_sha256=str(row["adapter_sha256"]),
            model_version=(
                str(row["model_version"])
                if row["model_version"] is not None
                else None
            ),
            row_count=int(row["row_count"]),
            server_count=int(row["server_count"]),
            first_seen=_isoformat(row["first_seen"]),
            last_seen=_isoformat(row["last_seen"]),
        )
        variants.append(variant)
        total_rows += variant.row_count

    return AdapterVariantsResponse(
        variants=variants,
        total_variants=len(variants),
        total_rows=total_rows,
    )


app = FastAPI(title="adapter_sha256_variant_api")
app.include_router(router)


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
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    adapter_sha256 TEXT,
                    model_version TEXT,
                    scored_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (id, server_id, axis_name, adapter_sha256, model_version, scored_at)
                VALUES
                    (1, 'server-1', 'risk', :adapter_a, 'model-v1', '2025-01-01T00:00:00'),
                    (2, 'server-2', 'risk', :adapter_a, 'model-v1', '2025-01-02T00:00:00'),
                    (3, 'server-1', 'quality', :adapter_a, 'model-v1', '2025-01-03T00:00:00'),
                    (4, 'server-3', 'risk', :adapter_b, 'model-v2', '2025-01-04T00:00:00')
                """
            ),
            {
                "adapter_a": "a" * 64,
                "adapter_b": "b" * 64,
            },
        )

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.include_router(router)

    with TestClient(that_app) as client:
        response = client.get("/api/scoring/adapter-variants")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_variants"] == 2
        assert payload["total_rows"] == 4
        assert any(
            variant["adapter_sha256"] == "a" * 64
            and variant["row_count"] == 3
            and variant["server_count"] == 2
            for variant in payload["variants"]
        )

    print("PASS")