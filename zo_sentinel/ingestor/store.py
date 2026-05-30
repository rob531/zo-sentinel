"""
store.py -- the storage seam for the artifact ingestor.

All mesh_memory access goes through a MeshStore so the ingestor is hermetic and
testable: tests use InMemoryMeshStore (no network), the host uses HttpMeshStore
(write_service at $ZO_WRITE_SERVICE, defaulting to the live ZoComputer port).

The ingestor writes back through this seam too -- verdicts, promotions,
quarantines, and reverse-fed fix-directives -- so a test can assert exactly what
WOULD hit mesh_memory without a live service.
"""
from __future__ import annotations

import os
from typing import Optional, Protocol

# Producer identity for build_artifact rows (the builder tier).
BUILDER_AGENT_ID = "t1.zo_sentinel_builder"
BUILD_ARTIFACT_TYPE = "build_artifact"

# Consumer identities the ingestor writes under.
INGESTOR_AGENT_ID = "zo_sentinel.artifact_ingestor"
DIRECTIVE_AGENT_ID = "zo_sentinel.directive"        # what goose_runner polls
BUILD_DIRECTIVE_TYPE = "build_directive"


class MeshStore(Protocol):
    def read_build_artifacts(self, since_built_at: Optional[str], limit: int) -> list[tuple[str, object]]: ...
    def get_watermark(self) -> Optional[str]: ...
    def set_watermark(self, value: str) -> None: ...
    def write(self, table: str, row: dict) -> bool: ...


class InMemoryMeshStore:
    """Hermetic store for tests. Seed `artifacts` with (row_id, content) pairs;
    inspect `writes` for everything the ingestor emitted."""

    def __init__(self, artifacts: Optional[list[tuple[str, object]]] = None):
        self._artifacts = list(artifacts or [])
        self._watermark: Optional[str] = None
        self.writes: list[tuple[str, dict]] = []

    def read_build_artifacts(self, since_built_at: Optional[str], limit: int) -> list[tuple[str, object]]:
        rows = self._artifacts
        if since_built_at:
            def _ts(c):
                if isinstance(c, dict):
                    return str(c.get("built_at", ""))
                return ""
            rows = [(rid, c) for (rid, c) in rows if _ts(c) > since_built_at]
        return rows[:limit]

    def get_watermark(self) -> Optional[str]:
        return self._watermark

    def set_watermark(self, value: str) -> None:
        self._watermark = value

    def write(self, table: str, row: dict) -> bool:
        self.writes.append((table, row))
        return True

    # test conveniences
    def writes_of_type(self, memory_type: str) -> list[dict]:
        return [r for (_t, r) in self.writes if r.get("memory_type") == memory_type]


class HttpMeshStore:
    """Live store: talks to write_service (DuckDB) over HTTP. Watermark is kept
    as a mesh_memory row so it survives restarts with no extra storage."""

    def __init__(self, base_url: Optional[str] = None, timeout: int = 15):
        self.base = (base_url or os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")).rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict) -> dict:
        import requests
        r = requests.post(self.base + path, json=payload, timeout=self.timeout)
        if r.status_code == 200:
            return r.json()
        return {}

    def read_build_artifacts(self, since_built_at: Optional[str], limit: int) -> list[tuple[str, object]]:
        sql = (
            "SELECT id, content FROM mesh_memory "
            f"WHERE agent_id = '{BUILDER_AGENT_ID}' "
            f"AND memory_type = '{BUILD_ARTIFACT_TYPE}' "
            "ORDER BY created_at ASC "
            f"LIMIT {int(limit)}"
        )
        resp = self._post("/query", {"sql": sql})
        out: list[tuple[str, object]] = []
        for row in resp.get("rows", []):
            out.append((str(row.get("id", "")), row.get("content")))
        return out

    def get_watermark(self) -> Optional[str]:
        sql = (
            "SELECT content FROM mesh_memory "
            f"WHERE agent_id = '{INGESTOR_AGENT_ID}' "
            "AND memory_type = 'ingest_watermark' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        resp = self._post("/query", {"sql": sql})
        rows = resp.get("rows", [])
        if rows:
            return str(rows[0].get("content", "")) or None
        return None

    def set_watermark(self, value: str) -> None:
        self.write("mesh_memory", {
            "agent_id": INGESTOR_AGENT_ID,
            "memory_type": "ingest_watermark",
            "content": value,
            "importance": 0.3,
        })

    def write(self, table: str, row: dict) -> bool:
        resp = self._post("/write", {"table": table, "rows": [row], "wait": True})
        return bool(resp.get("ok", resp.get("rows_written", 0)))
