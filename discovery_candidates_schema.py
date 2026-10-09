"""Canonical shape of `mcp_discovery_candidates` -- the single source of truth.

WHY THIS EXISTS (FU-596, 2026-10-09)
------------------------------------
Four ingestors each shipped their own `CREATE TABLE mcp_discovery_candidates`
with THREE incompatible shapes and there were ZERO migrations, so the table was
never actually created on the live bus.  Every discovery write then earned a 2xx
enqueue receipt and vanished (`Catalog Error: table does not exist`) or spooled
to `bus_write_guard`.  Net registry intake collapsed while ~8.5K candidates/day
piled into the spool.

The divergent DDLs were:
  * discovery_pypi_paginator_v2.ensure_table ...... server_id VARCHAR PK, package vocab
  * mcp_directory_ingestor.ensure_candidates_table  candidate_id INTEGER PK
  * mcp_reference_servers_ingestor.SCHEMA_CANDIDATES id BIGINT PK + UNIQUE(dir,name)
  * mcp_registry_ingestor.SCHEMA_CANDIDATES ........ id BIGINT PK + UNIQUE(dir,name)

CANONICAL DECISION
------------------
All five promoters (`auto_promoter`, `candidate_github_promoter`,
`candidate_npm_promoter`, `candidate_smithery_promoter`, `bulk_promote`) SELECT
`id, candidate_name, candidate_url, candidate_description,
discovered_in_directory, discovered_status, promoted` and key on `id`.  The 34K
spooled rows (github + npm) are in exactly this directory vocabulary and carry NO
id.  So the canonical shape is the reference/registry directory shape:

  * PK is `id BIGINT`, AUTO-DEFAULTED so id-less dict writers (github/npm/
    directory/pypi) work, while writers that supply a deterministic id
    (reference/registry) and their `ON CONFLICT (discovered_in_directory,
    candidate_name)` upserts keep working unchanged.
  * `UNIQUE (discovered_in_directory, candidate_name)` is the content key -- it
    is what makes a spool DRAIN idempotent and 0-loss.
  * `discovery_metadata` (VARCHAR / JSON text) preserves the rich package fields
    the pypi paginators used to carry in dedicated columns (version, author,
    downloads, license, ...).  No field is lost; only the extra columns are.

Every ingestor imports `CANDIDATE_COLUMNS` / the DDL helpers from here so a
divergent shape can never win a `CREATE TABLE IF NOT EXISTS` race again.  This
module imports nothing heavy at import time (ingestors run in a minimal env); the
SQLAlchemy builder used by the alembic migration is a lazy function.
"""

from __future__ import annotations

from typing import Any, Dict, List

TABLE_NAME = "mcp_discovery_candidates"
ID_SEQUENCE = "seq_mcp_discovery_candidates_id"
UNIQUE_CONSTRAINT = "uq_candidate_directory_name"

# The canonical column order.  Tests and the migration assert against this so the
# three definition sites (this module, the alembic migration, the live DDL) can
# never drift apart silently.
CANDIDATE_COLUMNS: List[str] = [
    "id",
    "candidate_name",
    "candidate_url",
    "candidate_description",
    "discovered_in_directory",
    "discovered_status",
    "promoted",
    "first_seen",
    "last_seen",
    "reviewed_at",
    "discovery_metadata",
]

# DuckDB / live-bus DDL, issued over write_service POST /execute.  A sequence is
# used (DuckDB has no AUTOINCREMENT keyword) so a row that omits `id` still gets
# one; a row that supplies an explicit deterministic id overrides the default.
CREATE_SEQUENCE_SQL = (
    f"CREATE SEQUENCE IF NOT EXISTS {ID_SEQUENCE} START 1"
)

CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id                      BIGINT PRIMARY KEY DEFAULT nextval('{ID_SEQUENCE}'),
    candidate_name          VARCHAR NOT NULL,
    candidate_url           VARCHAR,
    candidate_description   VARCHAR,
    discovered_in_directory VARCHAR NOT NULL,
    discovered_status       VARCHAR DEFAULT 'active',
    promoted                BOOLEAN DEFAULT FALSE,
    first_seen              TIMESTAMPTZ DEFAULT now(),
    last_seen               TIMESTAMPTZ DEFAULT now(),
    reviewed_at             TIMESTAMPTZ,
    discovery_metadata      VARCHAR,
    UNIQUE (discovered_in_directory, candidate_name)
)
""".strip()


def ddl_statements() -> List[str]:
    """Ordered DDL to materialise the canonical table on the live (DuckDB) bus."""
    return [CREATE_SEQUENCE_SQL, CREATE_TABLE_SQL]


def ensure_candidates_table(execute_fn) -> None:
    """Create the canonical table via `execute_fn(sql)` (an ingestor's ws_execute).

    `execute_fn` takes a single SQL string.  Both statements are
    `IF NOT EXISTS`, so this is idempotent and safe to call every cycle.  All
    ingestors route their table-creation through here, so no divergent shape can
    be created.
    """
    for sql in ddl_statements():
        execute_fn(sql)


def normalize_candidate(row: Dict[str, Any]) -> Dict[str, Any]:
    """Map any legacy ingestor vocabulary onto the canonical directory vocabulary.

    Accepts the three historical dialects and returns a dict whose keys are a
    subset of CANDIDATE_COLUMNS (id omitted -> auto-assigned by the sequence).
    Rich / unknown fields are folded into `discovery_metadata` rather than
    dropped, so nothing a crawler captured is lost.

      * directory vocab (github/npm/directory/reference/registry): passed through
      * package vocab (pypi_paginator_v2): name/source/url/... -> candidate_*
      * v1 pypi vocab: name/discovery_source/metadata_json/... -> candidate_*
    """
    import json

    r = dict(row)
    out: Dict[str, Any] = {}

    def first(*keys):
        for k in keys:
            if r.get(k) not in (None, ""):
                return r.get(k)
        return None

    name = first("candidate_name", "name")
    if name is not None:
        out["candidate_name"] = name
    url = first("candidate_url", "url", "home_page", "homepage", "repository", "pypi_url")
    if url is not None:
        out["candidate_url"] = url
    desc = first("candidate_description", "description", "summary")
    if desc is not None:
        out["candidate_description"] = str(desc)[:500]
    directory = first("discovered_in_directory", "source", "discovery_source")
    if directory is not None:
        out["discovered_in_directory"] = directory

    # status: the directory funnel convention is 'active'; the package ingestors'
    # private 'pending'/'candidate' states were honoured by no promoter, so they
    # are normalised to 'active' to actually enter the scoring funnel.
    status = first("discovered_status", "status")
    if status in (None, "pending", "candidate"):
        status = "active"
    out["discovered_status"] = status

    for passthrough in ("promoted", "first_seen", "last_seen", "reviewed_at", "id"):
        if passthrough in r:
            out[passthrough] = r[passthrough]

    # Fold everything that is NOT a canonical directory column into metadata so
    # the rich package fields (version/author/downloads/license/...) survive.
    consumed = {
        "candidate_name", "name", "candidate_url", "url", "home_page", "homepage",
        "repository", "pypi_url", "candidate_description", "description", "summary",
        "discovered_in_directory", "source", "discovery_source", "discovered_status",
        "status", "promoted", "first_seen", "last_seen", "reviewed_at", "id",
    }
    extra = {k: v for k, v in r.items() if k not in consumed and v not in (None, "")}
    existing_meta = first("discovery_metadata", "metadata", "metadata_json")
    meta: Dict[str, Any] = {}
    if existing_meta is not None:
        if isinstance(existing_meta, dict):
            meta.update(existing_meta)
        else:
            try:
                parsed = json.loads(existing_meta)
                if isinstance(parsed, dict):
                    meta.update(parsed)
                else:
                    meta["_raw"] = existing_meta
            except Exception:
                meta["_raw"] = str(existing_meta)
    meta.update(extra)
    if meta:
        out["discovery_metadata"] = json.dumps(meta, default=str, sort_keys=True)

    return out
