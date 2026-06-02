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

import json
import os
from typing import Optional, Protocol

# Producer identity for build_artifact rows (the builder tier).
BUILDER_AGENT_ID = "t1.zo_sentinel_builder"
BUILD_ARTIFACT_TYPE = "build_artifact"

# Consumer identities the ingestor writes under.
INGESTOR_AGENT_ID = "zo_sentinel.artifact_ingestor"
DIRECTIVE_AGENT_ID = "zo_sentinel.directive"        # what goose_runner polls
BUILD_DIRECTIVE_TYPE = "build_directive"


def _content_built_at(content: object) -> str:
    """built_at of a build_artifact row, tolerant of dict OR JSON-string content
    (the Http store returns JSON text; tests seed dicts-or-text)."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return ""
    if isinstance(content, dict):
        return str(content.get("built_at", ""))
    return ""


class MeshStore(Protocol):
    def read_build_artifacts(self, since_built_at: Optional[str], limit: int) -> list[tuple[str, object]]: ...
    def read_build_artifacts_since(self, since_created_at: Optional[str], limit: int) -> list[tuple[str, object, str]]:
        """Like read_build_artifacts, but ACTUALLY filters to rows with
        created_at > since_created_at (the plain read_build_artifacts ignores
        the bound in the Http impl). Returns (id, content, created_at) ordered
        by created_at ASC. Filters on the real created_at COLUMN, never on a
        json field of content -- json_extract over mesh_memory errors when the
        optimizer reaches non-object rows (e.g. ingest_watermark bare
        timestamps) before the type filter. The publisher's watermark relies on
        this honoring the bound so it never replays the backlog or stalls."""
        ...
    def get_watermark(self) -> Optional[str]: ...
    def set_watermark(self, value: str) -> None: ...
    def write(self, table: str, row: dict) -> bool: ...
    def read_latest(self, memory_type: str, agent_id: str) -> Optional[str]: ...


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

    def read_build_artifacts_since(self, since_created_at: Optional[str], limit: int) -> list[tuple[str, object, str]]:
        # Tests have no real created_at column; use the artifact's built_at as a
        # stand-in (monotonic with insertion order in fixtures), which matches
        # the Http store's created_at ordering closely enough for the publisher.
        rows = [(rid, c, _content_built_at(c)) for (rid, c) in self._artifacts]
        if since_created_at:
            rows = [t for t in rows if t[2] > since_created_at]
        rows.sort(key=lambda t: t[2])
        return rows[:limit]

    def get_watermark(self) -> Optional[str]:
        return self._watermark

    def set_watermark(self, value: str) -> None:
        self._watermark = value

    def write(self, table: str, row: dict) -> bool:
        self.writes.append((table, row))
        return True

    def read_latest(self, memory_type: str, agent_id: str) -> Optional[str]:
        for table, row in reversed(self.writes):
            if (table == "mesh_memory"
                    and row.get("memory_type") == memory_type
                    and row.get("agent_id") == agent_id):
                return row.get("content")
        return None

    # test conveniences
    def writes_of_type(self, memory_type: str) -> list[dict]:
        return [r for (_t, r) in self.writes if r.get("memory_type") == memory_type]


class HttpMeshStore:
    """Live store: talks to write_service (DuckDB) over HTTP. Watermark is kept
    as a mesh_memory row so it survives restarts with no extra storage."""

    def __init__(self, base_url: Optional[str] = None, timeout: int = 15,
                 connect_timeout: int = 3):
        self.base = (base_url or os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")).rstrip("/")
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.last_error: Optional[str] = None

    def _post(self, path: str, payload: dict) -> dict:
        """POST to write_service, degrading gracefully. A down/slow service
        must NOT crash a caller (matches the codebase's fire-and-forget ws_*
        helpers): on any error we record last_error and return {}."""
        try:
            import requests
            r = requests.post(self.base + path, json=payload,
                              timeout=(self.connect_timeout, self.timeout))
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return {}
        if r.status_code == 200:
            self.last_error = None
            return r.json()
        self.last_error = f"HTTP {r.status_code}: {r.text[:160]}"
        return {}

    def reachable(self) -> bool:
        """Quick /health probe so the CLI can report write_service state
        instead of hanging on the first query."""
        try:
            import requests
            r = requests.get(self.base + "/health", timeout=self.connect_timeout)
            ok = r.status_code == 200
            self.last_error = None if ok else f"HTTP {r.status_code}"
            return ok
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            return False

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

    def read_build_artifacts_since(self, since_created_at: Optional[str], limit: int) -> list[tuple[str, object, str]]:
        # Filter/order by the real created_at COLUMN (a timestamp), NEVER a json
        # field. json_extract_string(content,...) errors when DuckDB's optimizer
        # reaches non-object rows (e.g. ingest_watermark bare timestamps) before
        # the type filter, failing the whole query. created_at needs no parsing;
        # built_at is extracted in Python by the caller. since_created_at is our
        # own watermark, never user input; quote defensively all the same.
        where_since = ""
        if since_created_at:
            safe = str(since_created_at).replace("'", "''")
            where_since = f"AND created_at > '{safe}' "
        sql = (
            "SELECT id, content, created_at FROM mesh_memory "
            f"WHERE agent_id = '{BUILDER_AGENT_ID}' "
            f"AND memory_type = '{BUILD_ARTIFACT_TYPE}' "
            f"{where_since}"
            "ORDER BY created_at ASC "
            f"LIMIT {int(limit)}"
        )
        resp = self._post("/query", {"sql": sql})
        out: list[tuple[str, object, str]] = []
        for row in resp.get("rows", []):
            out.append((str(row.get("id", "")), row.get("content"),
                        str(row.get("created_at", ""))))
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

    def read_latest(self, memory_type: str, agent_id: str) -> Optional[str]:
        sql = (
            "SELECT content FROM mesh_memory "
            f"WHERE agent_id = '{agent_id}' "
            f"AND memory_type = '{memory_type}' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        resp = self._post("/query", {"sql": sql})
        rows = resp.get("rows", [])
        if rows:
            content = rows[0].get("content")
            return str(content) if content is not None else None
        return None
