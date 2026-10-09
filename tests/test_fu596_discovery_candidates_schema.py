"""FU-596 two-pole test: discovery-funnel schema reconciliation + 0-loss drain.

RED  (defect): with NO mcp_discovery_candidates table, the ingestor write path
     (bus_write_guard.guarded_write -- the exact mechanism github/npm use) posts
     0 rows and spools everything. Reproduces the live symptom (34K rows spooled
     since 2026-10-05, 0 lost).
GREEN (fix):   with the canonical schema applied to a throwaway sqlite DB, the
     repaired write path lands candidates in the table, AND replay_spool drains
     the previously-spooled rows into it with 0 loss (count in == count landed).

The test drives the REAL bus_write_guard code and the REAL canonical column set
(imported from discovery_candidates_schema), against a throwaway sqlite DB. It
never touches prod. sqlite is the portable stand-in for the live DuckDB bus:
INTEGER PRIMARY KEY auto-assigns like the canonical id sequence, and
UNIQUE(discovered_in_directory, candidate_name) enforces the content key.
"""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bus_write_guard as g  # noqa: E402
import discovery_candidates_schema as dcs  # noqa: E402

WRITE_URL = "http://bus/write"
QUERY_URL = "http://bus/query"

# sqlite translation of the canonical shape. The column SET is asserted equal to
# discovery_candidates_schema.CANDIDATE_COLUMNS so this can never drift from the
# live DDL / the alembic migration.
CANONICAL_SQLITE_DDL = """
CREATE TABLE mcp_discovery_candidates (
    id                      INTEGER PRIMARY KEY,
    candidate_name          TEXT NOT NULL,
    candidate_url           TEXT,
    candidate_description   TEXT,
    discovered_in_directory TEXT NOT NULL,
    discovered_status       TEXT DEFAULT 'active',
    promoted                INTEGER DEFAULT 0,
    first_seen              TEXT DEFAULT (datetime('now')),
    last_seen               TEXT DEFAULT (datetime('now')),
    reviewed_at             TEXT,
    discovery_metadata      TEXT,
    UNIQUE (discovered_in_directory, candidate_name)
)
"""

# What github_paginator.write_repos / npm_paginator.write_candidates spool: the
# directory vocabulary, NO id supplied.
GITHUB_ROWS = [
    {"candidate_name": f"octocat/mcp-{i}", "candidate_url": f"https://github.com/octocat/mcp-{i}",
     "candidate_description": f"server {i}", "discovered_in_directory": "github_topic",
     "discovered_status": "active", "last_seen": "2026-10-05T00:00:00Z", "promoted": False}
    for i in range(5)
]
NPM_ROWS = [
    {"candidate_name": f"@scope/mcp-{i}", "candidate_url": f"https://www.npmjs.com/package/mcp-{i}",
     "candidate_description": f"pkg {i}", "discovered_in_directory": "npm_search",
     "discovered_status": "active", "last_seen": "2026-10-06T00:00:00Z", "promoted": False}
    for i in range(3)
]


def _make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _querier(conn):
    """Answer information_schema.tables from sqlite_master (PRESENT/ABSENT)."""
    def q(query_url, sql, timeout):
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='mcp_discovery_candidates'"
        )
        rows = [{"table_name": r["name"]} for r in cur.fetchall()]
        return {"rows": rows}
    return q


def _poster(conn):
    """Insert a candidate dict, dedup on the canonical UNIQUE content key."""
    def post(write_url, table, row, timeout):
        cols = list(row.keys())
        collist = ",".join(cols)
        placeholders = ",".join("?" for _ in cols)
        sql = (
            f"INSERT INTO {table} ({collist}) VALUES ({placeholders}) "
            f"ON CONFLICT(discovered_in_directory, candidate_name) DO NOTHING"
        )
        vals = [int(v) if isinstance(v, bool) else v for v in row.values()]
        conn.execute(sql, vals)
        conn.commit()
        return True
    return post


def _count(conn):
    return conn.execute("SELECT COUNT(*) AS n FROM mcp_discovery_candidates").fetchone()["n"]


# --------------------------------------------------------------- anti-drift

def test_migration_columns_match_canonical_module():
    """The alembic migration 0014 must mirror CANDIDATE_COLUMNS exactly."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mig = os.path.join(here, "migrations", "versions",
                       "0014_discovery_candidates_canonical.py")
    ns = {}
    with open(mig, "r", encoding="utf-8") as fh:
        exec(compile(fh.read(), mig, "exec"), ns)  # noqa: S102 -- read our own file
    assert ns["_COLUMNS"] == dcs.CANDIDATE_COLUMNS


def test_sqlite_ddl_matches_canonical_column_set():
    conn = _make_conn()
    conn.executescript(CANONICAL_SQLITE_DDL)
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(mcp_discovery_candidates)")]
    assert set(cols) == set(dcs.CANDIDATE_COLUMNS)


# --------------------------------------------------------------- normalize

def test_normalize_package_vocab_to_directory_vocab():
    row = dcs.normalize_candidate({
        "name": "mcp-foo", "source": "pypi", "summary": "a desc",
        "home_page": "https://pypi.org/project/mcp-foo", "version": "1.2.3",
        "author": "me", "downloads_last_week": 42, "status": "pending",
    })
    assert row["candidate_name"] == "mcp-foo"
    assert row["discovered_in_directory"] == "pypi"
    assert row["candidate_description"] == "a desc"
    assert row["candidate_url"] == "https://pypi.org/project/mcp-foo"
    assert row["discovered_status"] == "active"  # 'pending' honoured by no promoter
    assert "id" not in row  # auto-assigned
    meta = json.loads(row["discovery_metadata"])
    assert meta["version"] == "1.2.3" and meta["author"] == "me" and meta["downloads_last_week"] == 42


def test_normalize_v1_pypi_vocab_merges_metadata_json():
    row = dcs.normalize_candidate({
        "name": "mcp-bar", "url": "u", "description": "d",
        "discovery_source": "pypi", "status": "candidate",
        "metadata_json": json.dumps({"version": "9"}), "author": "x",
    })
    assert row["candidate_name"] == "mcp-bar"
    assert row["discovered_in_directory"] == "pypi"
    assert row["discovered_status"] == "active"
    meta = json.loads(row["discovery_metadata"])
    assert meta["version"] == "9" and meta["author"] == "x"


def test_normalize_directory_vocab_passthrough():
    row = dcs.normalize_candidate({
        "candidate_name": "octocat/x", "candidate_url": "https://github.com/octocat/x",
        "candidate_description": "d", "discovered_in_directory": "github_topic",
        "discovered_status": "active", "promoted": False,
    })
    assert row["candidate_name"] == "octocat/x"
    assert row["discovered_in_directory"] == "github_topic"
    assert row["promoted"] is False
    assert "id" not in row


# --------------------------------------------------------------- RED pole

def test_RED_absent_table_posts_nothing_and_spools_everything(tmp_path):
    conn = _make_conn()  # NO table created -> the defect
    res = g.guarded_write(
        "mcp_discovery_candidates", GITHUB_ROWS,
        write_url=WRITE_URL, query_url=QUERY_URL, spool_dir=str(tmp_path),
        querier=_querier(conn), poster=_poster(conn),
        logger=lambda m: None, use_cache=False,
    )
    assert res.state == g.ABSENT
    assert res.posted == 0
    assert res.spooled == len(GITHUB_ROWS)
    spool = tmp_path / "mcp_discovery_candidates.jsonl"
    assert spool.exists()
    assert len(spool.read_text(encoding="utf-8").strip().splitlines()) == len(GITHUB_ROWS)


# --------------------------------------------------------------- GREEN pole

def test_GREEN_canonical_table_lands_writes_and_drains_spool_with_zero_loss(tmp_path):
    conn = _make_conn()
    querier, poster = _querier(conn), _poster(conn)

    # (1) Reproduce the defect: table absent -> github rows spool, 0 lost.
    red = g.guarded_write(
        "mcp_discovery_candidates", GITHUB_ROWS,
        write_url=WRITE_URL, query_url=QUERY_URL, spool_dir=str(tmp_path),
        querier=querier, poster=poster, logger=lambda m: None, use_cache=False,
    )
    assert red.posted == 0
    spooled_in = g.spool_depth("mcp_discovery_candidates", str(tmp_path))
    assert spooled_in == len(GITHUB_ROWS)

    # (2) Apply the canonical schema (what migration 0014 does on prod).
    conn.executescript(CANONICAL_SQLITE_DDL)

    # (3) New candidates now LAND in the table (not the spool).
    new = g.guarded_write(
        "mcp_discovery_candidates", NPM_ROWS,
        write_url=WRITE_URL, query_url=QUERY_URL, spool_dir=str(tmp_path),
        querier=querier, poster=poster, logger=lambda m: None, use_cache=False,
    )
    assert new.state == g.PRESENT
    assert new.posted == len(NPM_ROWS)
    assert new.spooled == 0
    assert _count(conn) == len(NPM_ROWS)

    # (4) DRAIN the spool into the table -- 0 loss: rows in == rows landed.
    before = _count(conn)
    drain = g.replay_spool(
        "mcp_discovery_candidates",
        write_url=WRITE_URL, query_url=QUERY_URL, spool_dir=str(tmp_path),
        querier=querier, poster=poster, logger=lambda m: None,
    )
    assert drain.state == g.PRESENT
    assert drain.posted == spooled_in                    # every spooled row posted
    assert drain.failed == 0
    assert g.spool_depth("mcp_discovery_candidates", str(tmp_path)) == 0
    landed = _count(conn) - before
    assert landed == spooled_in                          # rows in == rows landed (0 loss)
    assert _count(conn) == len(NPM_ROWS) + len(GITHUB_ROWS)

    # (5) id auto-assigned (no id was ever supplied) and unique.
    ids = [r["id"] for r in conn.execute("SELECT id FROM mcp_discovery_candidates")]
    assert all(i is not None for i in ids)
    assert len(set(ids)) == len(ids)


def test_GREEN_redelivery_is_idempotent_no_dup_no_loss(tmp_path):
    conn = _make_conn()
    conn.executescript(CANONICAL_SQLITE_DDL)
    querier, poster = _querier(conn), _poster(conn)
    kwargs = dict(write_url=WRITE_URL, query_url=QUERY_URL, spool_dir=str(tmp_path),
                  querier=querier, poster=poster, logger=lambda m: None, use_cache=False)

    g.guarded_write("mcp_discovery_candidates", NPM_ROWS, **kwargs)
    first = _count(conn)
    # Re-deliver the SAME page: UNIQUE(directory,name) dedups -> no dup, no error.
    g.guarded_write("mcp_discovery_candidates", NPM_ROWS, **kwargs)
    assert _count(conn) == first == len(NPM_ROWS)


def test_GREEN_explicit_id_and_idless_rows_coexist(tmp_path):
    """reference/registry supply a deterministic id; github/npm omit it."""
    conn = _make_conn()
    conn.executescript(CANONICAL_SQLITE_DDL)
    poster = _poster(conn)
    # reference-style explicit id
    poster(WRITE_URL, "mcp_discovery_candidates", {
        "id": 1234567, "candidate_name": "ref/server", "candidate_url": "u",
        "candidate_description": "d", "discovered_in_directory": "anthropic_reference",
        "discovered_status": "active"}, 10)
    # github-style id-less (auto-assigned)
    poster(WRITE_URL, "mcp_discovery_candidates", {
        "candidate_name": "octocat/x", "candidate_url": "u2",
        "candidate_description": "d2", "discovered_in_directory": "github_topic",
        "discovered_status": "active", "promoted": False}, 10)
    rows = conn.execute(
        "SELECT id, discovered_in_directory FROM mcp_discovery_candidates ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert {r["discovered_in_directory"] for r in rows} == {"anthropic_reference", "github_topic"}
    assert all(r["id"] is not None for r in rows)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
