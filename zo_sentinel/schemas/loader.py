"""
zo_sentinel.schemas.loader
==========================

Single source of truth for probe / evaluator code that needs to know what
columns live in the warehouse.

Two snapshot files live at the repo root under ``schemas/``:

* ``schemas/duckdb_schema.json``       -- the DuckDB warehouse (write_service)
* ``schemas/mesh_memory_schema.json``  -- the SQLite mesh_memory store

Both are regenerated tower-side by ``refresh_schema_doc.py`` (DuckDB via the
loopback write_service, SQLite via ``PRAGMA table_info``). The committed
snapshot is what every probe consumes; the uptime/drift probe is the
mechanism that surfaces divergence between snapshot and live DB.

Design constraints
------------------

* **Stdlib only.** This module is imported by lightweight diagnostics
  and CI smoke tests that run on minimal Python environments. No
  ``requests``, no ``pydantic``, no ``duckdb``.
* **Cross-platform.** Probes call this from the tower
  (``/home/workspace/zo_sentinel/``) and CI calls it from GH runners
  and a Windows workstation. Repo-root resolution must work everywhere
  without env-var hacks.
* **Fail loud.** Missing snapshot, unparseable JSON, or a snapshot
  older than ``STALE_AFTER_DAYS`` raises rather than silently returning
  an empty schema -- the recurring failure mode this whole effort
  exists to prevent is silent column-name drift.

Public surface
--------------

::

    from zo_sentinel.schemas.loader import (
        load_duckdb_schema,
        load_mesh_memory_schema,
        ColumnSpec,
        SchemaSnapshot,
        StaleSchemaError,
    )

    schema = load_mesh_memory_schema()
    cols = schema.column_names("mesh_memory")
    errs = schema.validate_row("mesh_memory", row)
    if errs:
        raise ValueError(errs)
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional


# How old can a committed snapshot be before consumers should treat it as
# probably-wrong? 14 days matches the cadence Robin keeps for tower-side
# refresh + commit cycles.
STALE_AFTER_DAYS = 14


class StaleSchemaError(RuntimeError):
    """Raised when a snapshot is older than STALE_AFTER_DAYS and the caller
    opted into strict freshness via ``ensure_fresh=True``."""


class ColumnSpec(NamedTuple):
    name: str
    type: str
    ordinal: int
    nullable: bool


class SchemaSnapshot:
    """Read-only view over a committed schema snapshot file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(
                f"Schema snapshot not found: {self.path}. "
                "Run refresh_schema_doc.py tower-side to regenerate."
            )
        try:
            self._raw: Dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Schema snapshot {self.path} is not valid JSON: {e}"
            ) from e

        if "tables" not in self._raw or not isinstance(self._raw["tables"], dict):
            raise ValueError(
                f"Schema snapshot {self.path} missing required 'tables' object"
            )

    # ---- metadata ---------------------------------------------------------

    @property
    def engine(self) -> str:
        return str(self._raw.get("engine", "unknown"))

    @property
    def source(self) -> str:
        return str(self._raw.get("source", ""))

    @property
    def schema_hash(self) -> str:
        return str(self._raw.get("schema_hash", ""))

    @property
    def generated_at(self) -> datetime:
        """Parse the ``generated_at`` ISO-8601 timestamp.

        Tolerates both ``...Z`` and ``...+00:00`` suffixes. Always returns a
        timezone-aware datetime in UTC. Raises ValueError if missing /
        unparseable rather than guessing -- a snapshot with no timestamp is
        suspect.
        """
        raw = self._raw.get("generated_at")
        if not raw:
            raise ValueError(
                f"Schema snapshot {self.path} missing 'generated_at'"
            )
        # Normalise Z -> +00:00 for fromisoformat compatibility on 3.10
        normalised = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @property
    def is_stale(self) -> bool:
        """True if the snapshot is older than STALE_AFTER_DAYS."""
        age = datetime.now(timezone.utc) - self.generated_at
        return age > timedelta(days=STALE_AFTER_DAYS)

    def assert_fresh(self) -> None:
        """Raise StaleSchemaError if the snapshot is past its shelf life."""
        if self.is_stale:
            raise StaleSchemaError(
                f"Schema snapshot {self.path} generated_at "
                f"{self.generated_at.isoformat()} is older than "
                f"{STALE_AFTER_DAYS} days. Regenerate via "
                "refresh_schema_doc.py on the tower and commit."
            )

    # ---- table / column accessors -----------------------------------------

    def tables(self) -> List[str]:
        return sorted(self._raw["tables"].keys())

    def has_table(self, table: str) -> bool:
        return table in self._raw["tables"]

    def columns(self, table: str) -> List[ColumnSpec]:
        if not self.has_table(table):
            raise KeyError(
                f"Table {table!r} not in schema snapshot {self.path}. "
                f"Known tables: {self.tables()}"
            )
        raw_cols = self._raw["tables"][table]
        out: List[ColumnSpec] = []
        for c in raw_cols:
            out.append(
                ColumnSpec(
                    name=c["name"],
                    type=c["type"],
                    ordinal=int(c.get("ordinal", 0)),
                    nullable=bool(c.get("nullable", True)),
                )
            )
        # Ordinal-sort so column_names() is canonical regardless of
        # accidental file-edit reordering.
        out.sort(key=lambda x: (x.ordinal, x.name))
        return out

    def column_names(self, table: str) -> List[str]:
        return [c.name for c in self.columns(table)]

    def column_types(self, table: str) -> Dict[str, str]:
        return {c.name: c.type for c in self.columns(table)}

    def validate_row(self, table: str, row: Dict[str, Any]) -> List[str]:
        """Return a list of validation error messages.

        Empty list = row is structurally valid against the snapshot.
        Specifically flags:
          * unknown columns (probe is naming a column not in the schema)
          * missing non-nullable columns (probe forgot a required field)

        Does NOT type-check values -- the snapshot stores the column type
        as the DB engine's native string (VARCHAR, TIMESTAMPTZ, BIGINT)
        and mapping Python types to those is engine-specific and lossy.
        """
        if not self.has_table(table):
            return [f"unknown table: {table!r}"]
        errors: List[str] = []
        cols = self.columns(table)
        col_names = {c.name for c in cols}
        for k in row.keys():
            if k not in col_names:
                errors.append(f"unknown column: {k!r} not in {table}")
        for c in cols:
            if not c.nullable and c.name not in row:
                errors.append(f"missing required column: {c.name!r}")
        return errors

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"SchemaSnapshot(path={self.path!r}, engine={self.engine!r}, "
            f"tables={len(self.tables())}, hash={self.schema_hash[:12]!r})"
        )


# ---------------------------------------------------------------------------
# Repo-root resolution + convenience loaders
# ---------------------------------------------------------------------------


def _find_repo_root(start: Optional[Path] = None) -> Path:
    """Walk up from ``start`` (or this file) until we find a directory that
    looks like the zo-sentinel repo root.

    The reliable anchor is ``builder_conventions.json`` (committed at the
    repo root, exists since the very first commit). ``.git`` is a fallback
    for shallow checkouts. We deliberately do NOT use the existence of a
    ``schemas/`` subdirectory as the anchor, because the package directory
    ``zo_sentinel/schemas/`` shares that name and would otherwise short-
    circuit the walk.
    """
    here = (start or Path(__file__)).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "builder_conventions.json").is_file():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    # Fallback: zo_sentinel/schemas/loader.py -> repo root is parents[2]
    return Path(__file__).resolve().parents[2]


def _schemas_dir(start: Optional[Path] = None) -> Path:
    return _find_repo_root(start) / "schemas"


def load_duckdb_schema(
    path: Optional[Path] = None,
    ensure_fresh: bool = False,
) -> SchemaSnapshot:
    """Load the DuckDB warehouse schema snapshot.

    Args:
        path: override path to the JSON file. Default: schemas/duckdb_schema.json
              relative to the repo root.
        ensure_fresh: if True, raise StaleSchemaError when the snapshot is
                      older than STALE_AFTER_DAYS. Probes that need to be
                      sure they're not driving on stale data should set this.
    """
    p = Path(path) if path is not None else _schemas_dir() / "duckdb_schema.json"
    snap = SchemaSnapshot(p)
    if ensure_fresh:
        snap.assert_fresh()
    return snap


def load_mesh_memory_schema(
    path: Optional[Path] = None,
    ensure_fresh: bool = False,
) -> SchemaSnapshot:
    """Load the mesh_memory (SQLite) schema snapshot. See ``load_duckdb_schema``."""
    p = (
        Path(path)
        if path is not None
        else _schemas_dir() / "mesh_memory_schema.json"
    )
    snap = SchemaSnapshot(p)
    if ensure_fresh:
        snap.assert_fresh()
    return snap


# ---------------------------------------------------------------------------
# Hashing helper (used by the refresh script AND by the drift probe so they
# always agree on what "the same schema" means)
# ---------------------------------------------------------------------------


def compute_schema_hash(rows: List[Dict[str, Any]]) -> str:
    """Stable sha256 of (table, column, type) tuples sorted lexicographically.

    ``rows`` is a list of dicts with keys ``table_name``, ``column_name``,
    ``data_type`` -- the shape returned by both ``information_schema.columns``
    (DuckDB) and ``PRAGMA table_info`` after the refresh script normalises it.

    Stable across re-orderings of the input list, which is the whole point.
    """
    import hashlib

    tuples = sorted(
        (r["table_name"], r["column_name"], r["data_type"]) for r in rows
    )
    h = hashlib.sha256()
    for t, c, dt in tuples:
        h.update(f"{t}|{c}|{dt}\n".encode("utf-8"))
    return h.hexdigest()
