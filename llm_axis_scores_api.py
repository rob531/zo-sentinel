# llm_axis_scores_api.py
"""
FastAPI router exposing GET `/servers/{server_id}/llm_scores`.

The endpoint reads LLM axis scores for a given `server_id` from the
`mcp_llm_axis_scores` table **via** the `write_service` – an HTTP POST to
`/query` with a parameterised SQL statement.  The implementation uses
FastAPI + Pydantic for request/response models and does **not** import
`duckdb` directly.

A self‑contained ``__main__`` block creates an in‑memory store,
mounts a mock ``/query`` write‑service, runs a test client against the
router and prints ``PASS`` when the response matches the expected
structure.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class LlmAxisScore(BaseModel):
    """Single axis score entry."""

    axis_name: str = Field(..., description="Name of the LLM axis")
    score: float = Field(..., description="Score for the axis")
    metadata: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional extra information"
    )


class LlmAxisScoresResponse(BaseModel):
    """Response model for the GET endpoint."""

    server_id: int = Field(..., description="ID of the server")
    llm_axis_scores: List[LlmAxisScore] = Field(
        ..., description="List of axis scores for the server"
    )


# --------------------------------------------------------------------------- #
# Router implementation
# --------------------------------------------------------------------------- #

router = APIRouter()


@router.get(
    "/servers/{server_id}/llm_scores",
    response_model=LlmAxisScoresResponse,
    response_model_exclude_unset=True,
    status_code=status.HTTP_200_OK,
)
async def get_llm_axis_scores(server_id: int, request: Request) -> JSONResponse:
    """
    Retrieve LLM axis scores for ``server_id`` via the write‑service.

    The write‑service expects a JSON payload:
        {
            "sql": "<parameterised sql>",
            "params": [<values>]
        }

    It returns a JSON object with a ``rows`` key containing a list of
    dictionaries that map column names to values.
    """
    # ------------------------------------------------------------------- #
    # 1. Build the query payload
    # ------------------------------------------------------------------- #
    sql = (
        "SELECT axis_name, score, metadata "
        "FROM mcp_llm_axis_scores "
        "WHERE server_id = ?"
    )
    payload = {"sql": sql, "params": [server_id]}

    # ------------------------------------------------------------------- #
    # 2. Call the write‑service (POST /query)
    # ------------------------------------------------------------------- #
    # ``request.base_url`` points at the current test server (e.g.
    # ``http://testserver/``).  Using an ``AsyncClient`` keeps the call
    # fully async and works both in production and in the test client.
    async with httpx.AsyncClient(base_url=str(request.base_url)) as client:
        resp = await client.post("/query", json=payload)
        resp.raise_for_status()
        data = resp.json()

    # ------------------------------------------------------------------- #
    # 3. Transform DB rows into Pydantic models
    # ------------------------------------------------------------------- #
    rows: List[Dict[str, Any]] = data.get("rows", [])
    scores = [
        LlmAxisScore(
            axis_name=row["axis_name"],
            score=row["score"],
            metadata=row.get("metadata"),
        )
        for row in rows
    ]

    response = LlmAxisScoresResponse(server_id=server_id, llm_axis_scores=scores)
    return JSONResponse(content=response.dict())


# --------------------------------------------------------------------------- #
# Mock write‑service implementation (used only for the self‑contained test)
# --------------------------------------------------------------------------- #


def _create_mock_write_service(app: FastAPI) -> None:
    """
    Mounts a ``/query`` endpoint on ``app`` that mimics the write‑service.

    The endpoint expects a JSON body with ``sql`` and ``params`` and returns
    rows from an in‑memory ``app.state.llm_store`` dictionary.
    """

    @app.post("/query")
    async def query_endpoint(payload: Dict[str, Any]) -> JSONResponse:
        sql: str = payload.get("sql", "")
        params: List[Any] = payload.get("params", [])

        # Very naive parsing – we only support the exact query used above.
        if (
            "FROM mcp_llm_axis_scores" not in sql
            or "WHERE server_id = ?" not in sql
            or len(params) != 1
        ):
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"error": "Unsupported query"},
            )

        server_id = params[0]
        store: Dict[int, List[Dict[str, Any]]] = app.state.llm_store
        rows = store.get(server_id, [])
        return JSONResponse(content={"rows": rows})


# --------------------------------------------------------------------------- #
# __main__ block – runs a tiny integration test using TestClient
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import json
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------- #
    # 1. Build the FastAPI app and mount router + mock write‑service
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)

    # In‑memory store: {server_id: [row_dict, ...]}
    # ``metadata`` can be any JSON‑serialisable object; we use a simple dict.
    app.state.llm_store = {
        42: [
            {
                "axis_name": "creativity",
                "score": 8.7,
                "metadata": {"source": "test"},
            },
            {
                "axis_name": "reliability",
                "score": 9.3,
                "metadata": {"source": "test"},
            },
        ],
        99: [],  # server with no scores
    }

    # Attach the mock write‑service endpoint.
    _create_mock_write_service(app)

    # ------------------------------------------------------------------- #
    # 2. Run the test client
    # ------------------------------------------------------------------- #
    client = TestClient(app)

    # Known server_id (42) – should return two scores.
    resp = client.get("/servers/42/llm_scores")
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"
    payload = resp.json()

    # Expected structure
    expected = {
        "server_id": 42,
        "llm_axis_scores": [
            {
                "axis_name": "creativity",
                "score": 8.7,
                "metadata": {"source": "test"},
            },
            {
                "axis_name": "reliability",
                "score": 9.3,
                "metadata": {"source": "test"},
            },
        ],
    }

    # Normalise JSON (order of list items matters – we keep the order as stored)
    assert payload == expected, f"Response mismatch:\n{json.dumps(payload, indent=2)}"

    # Also test a server with no scores (99)
    resp_empty = client.get("/servers/99/llm_scores")
    assert resp_empty.status_code == 200
    assert resp_empty.json() == {"server_id": 99, "llm_axis_scores": []}

    print("PASS")