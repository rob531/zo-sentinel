from __future__ import annotations

import importlib
import inspect
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _load_real_data_layer() -> Optional[Any]:
    candidates = (
        "services.staged.ask_corpus_orphan_api.data",
        "services.staged.ask_corpus_orphan_api.db",
        "services.staged.ask_corpus_orphan_api.database",
        "services.database",
        "services.db",
        "services.data",
        "services._exemplar.data",
    )
    for module_name in candidates:
        try:
            return importlib.import_module(module_name)
        except ImportError:
            continue
    return None


REAL_DATA_LAYER = _load_real_data_layer()


def _find_real_db_provider() -> Optional[Any]:
    if REAL_DATA_LAYER is None:
        return None
    for name in (
        "get_db_connection",
        "get_db",
        "get_connection",
        "get_session",
        "db_session",
    ):
        candidate = getattr(REAL_DATA_LAYER, name, None)
        if callable(candidate):
            return candidate
    return None


REAL_DB_PROVIDER = _find_real_db_provider()


class OrphanDocument(BaseModel):
    server_id: str
    content_hash: str
    indexed_at: datetime


class UnindexedServer(BaseModel):
    server_id: str
    name: str


class OrphanSummary(BaseModel):
    orphan_document_count: int
    orphan_server_count: int
    unindexed_server_count: int


class OrphanResponse(BaseModel):
    orphan_documents: List[OrphanDocument] = Field(default_factory=list)
    unindexed_servers: List[UnindexedServer] = Field(default_factory=list)
    summary: OrphanSummary


app = FastAPI(title="Ask Corpus Orphan API")


def get_db_connection() -> Iterator[Any]:
    if REAL_DB_PROVIDER is None:
        raise RuntimeError("The real database provider could not be imported")

    resource = REAL_DB_PROVIDER()
    if inspect.isgenerator(resource):
        resource = next(resource)

    try:
        yield resource
    finally:
        close = getattr(resource, "close", None)
        if callable(close):
            close()


get_db = get_db_connection
get_session = get_db_connection


def _execute(
    db: Any,
    statement: str,
    parameters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    parameters = parameters or {}
    result = db.execute(text(statement), parameters)
    if hasattr(result, "mappings"):
        return [dict(row) for row in result.mappings().all()]
    return [dict(row) for row in result.fetchall()]


@app.get(
    "/api/ask-corpus/orphans",
    response_model=OrphanResponse,
)
def get_ask_corpus_orphans(
    limit: int = Query(default=200, ge=1, le=1000),
    registry_source: Optional[str] = Query(default=None),
    db: Any = Depends(get_db_connection),
) -> OrphanResponse:
    orphan_rows = _execute(
        db,
        """
        SELECT
            ask_corpus_index.server_id,
            ask_corpus_index.content_hash,
            ask_corpus_index.indexed_at
        FROM ask_corpus_index
        LEFT JOIN mcp_server_registry
            ON mcp_server_registry.server_id = ask_corpus_index.server_id
        WHERE mcp_server_registry.server_id IS NULL
        ORDER BY ask_corpus_index.indexed_at DESC
        LIMIT :limit
        """,
        {"limit": limit},
    )

    unindexed_parameters: Dict[str, Any] = {"limit": limit}
    source_clause = ""
    if registry_source is not None:
        source_clause = "AND mcp_server_registry.registry_source = :registry_source"
        unindexed_parameters["registry_source"] = registry_source

    unindexed_rows = _execute(
        db,
        f"""
        SELECT
            mcp_server_registry.server_id,
            mcp_server_registry.name
        FROM mcp_server_registry
        LEFT JOIN ask_corpus_index
            ON ask_corpus_index.server_id = mcp_server_registry.server_id
        WHERE ask_corpus_index.server_id IS NULL
        {source_clause}
        ORDER BY mcp_server_registry.name ASC
        LIMIT :limit
        """,
        unindexed_parameters,
    )

    orphan_count_rows = _execute(
        db,
        """
        SELECT
            COUNT(*) AS orphan_document_count,
            COUNT(DISTINCT ask_corpus_index.server_id) AS orphan_server_count
        FROM ask_corpus_index
        LEFT JOIN mcp_server_registry
            ON mcp_server_registry.server_id = ask_corpus_index.server_id
        WHERE mcp_server_registry.server_id IS NULL
        """,
    )

    unindexed_count_rows = _execute(
        db,
        f"""
        SELECT COUNT(*) AS unindexed_server_count
        FROM mcp_server_registry
        LEFT JOIN ask_corpus_index
            ON ask_corpus_index.server_id = mcp_server_registry.server_id
        WHERE ask_corpus_index.server_id IS NULL
        {source_clause}
        """,
        unindexed_parameters,
    )

    orphan_count = orphan_count_rows[0] if orphan_count_rows else {}
    unindexed_count = unindexed_count_rows[0] if unindexed_count_rows else {}

    return OrphanResponse(
        orphan_documents=[OrphanDocument(**row) for row in orphan_rows],
        unindexed_servers=[UnindexedServer(**row) for row in unindexed_rows],
        summary=OrphanSummary(
            orphan_document_count=int(orphan_count.get("orphan_document_count", 0)),
            orphan_server_count=int(orphan_count.get("orphan_server_count", 0)),
            unindexed_server_count=int(
                unindexed_count.get("unindexed_server_count", 0)
            ),
        ),
    )


def _seed_contract_database(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ask_corpus_index (
                    server_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    indexed_at TIMESTAMP NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    registry_source TEXT NOT NULL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO ask_corpus_index
                    (server_id, content_hash, indexed_at)
                VALUES
                    (:shared_server_id, :shared_hash, :shared_indexed_at),
                    (:orphan_server_id, :orphan_hash, :orphan_indexed_at)
                """
            ),
            {
                "shared_server_id": "shared-server",
                "shared_hash": "shared-hash",
                "shared_indexed_at": datetime(2024, 1, 1, 12, 0, 0),
                "orphan_server_id": "orphan-server",
                "orphan_hash": "orphan-hash",
                "orphan_indexed_at": datetime(2024, 1, 2, 12, 0, 0),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source)
                VALUES
                    (:shared_server_id, :shared_name, :shared_source),
                    (:unindexed_server_id, :unindexed_name, :unindexed_source)
                """
            ),
            {
                "shared_server_id": "shared-server",
                "shared_name": "Shared Server",
                "shared_source": "primary",
                "unindexed_server_id": "unindexed-server",
                "unindexed_name": "Unindexed Server",
                "unindexed_source": "primary",
            },
        )


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    _seed_contract_database(engine)

    def override_get_db_connection() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db_connection] = override_get_db_connection

    try:
        with TestClient(app) as client:
            response = client.get("/api/ask-corpus/orphans")

            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["summary"]["orphan_document_count"] == 1, payload
            assert payload["summary"]["orphan_server_count"] == 1, payload
            assert payload["summary"]["unindexed_server_count"] == 1, payload
            assert (
                payload["orphan_documents"][0]["server_id"] == "orphan-server"
            ), payload
            assert (
                payload["unindexed_servers"][0]["server_id"]
                == "unindexed-server"
            ), payload
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    run_self_test()