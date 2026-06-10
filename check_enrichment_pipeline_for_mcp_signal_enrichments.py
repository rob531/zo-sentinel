# deps: requests
"""
Diagnostic utility: inspect the enrichment pipeline to explain why
mcp_signal_enrichments is empty (0 rows), and report which enrichment
modules are registered to write to it.

No DB writes — SELECT queries only via write_service /query.
No import of duckdb, protected modules, or any enrichment modules.
"""
import json
import os
import sys
import glob
import textwrap

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
TIMEOUT = 10


def ws_query(sql: str) -> list[dict]:
    """Run a SELECT query via write_service /query and return rows."""
    import requests
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": sql},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "rows" in data:
        return data["rows"]
    return data if isinstance(data, list) else []


def ws_execute(sql: str) -> None:
    """Execute DDL/DML via write_service /execute (not used by this diagnostic)."""
    import requests
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/execute",
        json={"sql": sql, "wait": True},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()


def get_columns(table_name: str) -> list[str]:
    """Return column names for the given table via information_schema."""
    sql = textwrap.dedent(f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = '{table_name}'
        ORDER BY ordinal_position
    """).strip()
    rows = ws_query(sql)
    return [r.get("column_name") or r.get("columnName") or r.get("COLUMN_NAME", "") for r in rows]


def get_row_count(table_name: str) -> int:
    """Return the row count for the given table."""
    sql = f"SELECT COUNT(*) AS cnt FROM {table_name}"
    rows = ws_query(sql)
    if rows:
        row = rows[0]
        for key in ("cnt", "count_star", "COUNT(*)"):
            if key in row:
                return int(row[key])
        # fallback: first value
        return int(list(row.values())[0])
    return 0


def get_sample_rows(table_name: str, limit: int = 5) -> list[dict]:
    """Return sample rows from the table."""
    sql = f"SELECT * FROM {table_name} LIMIT {limit}"
    return ws_query(sql)


def list_enrichment_modules() -> list[str]:
    """Return sorted list of *enrichment*.py files in the repo root."""
    base = "/home/workspace/zo_sentinel"
    patterns = [
        os.path.join(base, "*enrichment*.py"),
        os.path.join(base, "enrichers", "*enrichment*.py"),
        os.path.join(base, "enrichers", "*enrichment*.py"),
    ]
    found = set()
    for pattern in patterns:
        for path in glob.glob(pattern):
            if os.path.isfile(path) and not path.endswith("__pycache__"):
                found.add(os.path.basename(path))
    return sorted(found)


def derive_root_cause(
    enrichment_count: int,
    signal_scores_count: int,
    columns: list[str],
    enrichment_modules: list[str],
) -> str:
    """
    Derive a root cause hypothesis from gathered data.
    Returns one of: "no enricher writing to table",
                    "enricher produces zero rows",
                    "schema mismatch",
                    "unknown"
    """
    # If there are no enrichment modules on disk, no enricher can write
    if not enrichment_modules:
        return "no enricher writing to table"

    # If signal_scores is populated but enrichments is empty,
    # the pipeline likely has no writer for mcp_signal_enrichments
    if signal_scores_count > 0 and enrichment_count == 0:
        return "no enricher writing to table"

    # If both are empty, the corpus itself might be empty or the enricher
    # produces zero rows
    if signal_scores_count == 0 and enrichment_count == 0:
        return "enricher produces zero rows"

    # Schema mismatch — if columns are present but no rows, enrichers
    # might be writing to a different table
    if enrichment_count == 0 and columns:
        return "schema mismatch"

    return "unknown"


def main() -> int:
    table = "mcp_signal_enrichments"
    signal_scores_table = "mcp_signal_scores"

    # 1. Table schema
    columns = get_columns(table)
    if not columns:
        # table may not exist at all
        columns = []

    # 2. Enrichment table row count
    enrichment_count = get_row_count(table)

    # 3. Signal scores row count
    signal_scores_count = get_row_count(signal_scores_table)

    # 4. Enrichment modules on disk
    enrichment_modules = list_enrichment_modules()

    # 5. Root cause hypothesis
    root_cause = derive_root_cause(
        enrichment_count=enrichment_count,
        signal_scores_count=signal_scores_count,
        columns=columns,
        enrichment_modules=enrichment_modules,
    )

    # Print report
    print("=" * 60)
    print(" mcp_signal_enrichments pipeline diagnostic report")
    print("=" * 60)

    print()
    print("1. Table schema")
    print("-" * 40)
    if columns:
        for col in columns:
            print(f"   {col}")
    else:
        print("   (no columns found — table may not exist)")

    print()
    print("2. Enrichment table row count")
    print("-" * 40)
    print(f"   mcp_signal_enrichments: {enrichment_count}")

    print()
    print("3. Signal scores row count")
    print("-" * 40)
    print(f"   mcp_signal_scores: {signal_scores_count}")

    print()
    print("4. Enrichment modules on disk")
    print("-" * 40)
    if enrichment_modules:
        for mod in enrichment_modules:
            print(f"   {mod}")
    else:
        print("   (no *enrichment*.py files found)")

    print()
    print("5. Root cause hypothesis")
    print("-" * 40)
    print(f"   {root_cause}")

    print()
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())