#!/usr/bin/env python3
"""cycle-0194 (chairman issue #4003 / FU-596) negative controls.

Two cures, each with a control that is RED against origin/main and GREEN here:

  1. bootstrap_missing.py gains a `mcp_discovery_candidates` definition (the table the
     discovery -> promotion funnel writes to and the promoters read from; absent from
     every file that runs, so write_service discarded every candidate with a Catalog
     Error until #6167 began spooling them).  Verified structurally AND by executing the
     extracted DDL in an in-memory DuckDB, inserting a paginator-shaped row with no id,
     and running the promoter's own SELECT/UPDATE-by-id against it.

  2. discovery_github_paginator.py and discovery_npm_paginator.py call
     bus_write_guard.replay_spool (via a drain_spool() helper) at the top of cycle().
     bus_write_guard shipped the spool in #6167 but replay_spool had NO production caller
     anywhere, so spooled rows never drained even after the table existed.  Verified
     structurally (cycle() calls drain_spool, module references replay_spool) AND
     behaviourally against bus_write_guard with injected doubles: replay is a no-op while
     the target is ABSENT (posts 0, leaves the spool intact -- the loss-prevention the
     funnel depends on) and drains fully once it is PRESENT.

Run as a negative control against any tree:

    python tests/test_c194_discovery_candidates.py <tree>     # rc 0 all green / 1 some red

With no argument it checks its own repo (so pytest collects it too).
"""
import argparse
import ast
import importlib.util
import os
import sys
import tempfile

CONTRACT_COLUMNS = {
    # written by both paginators
    "candidate_name", "candidate_url", "candidate_description",
    "discovered_in_directory", "discovered_status", "last_seen", "promoted",
    # read / updated by both promoters
    "id", "reviewed_at", "metadata",
}
PAGINATORS = ("discovery_github_paginator.py", "discovery_npm_paginator.py")


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _extract_table_sql(bootstrap_src, label):
    """Pull the CREATE TABLE string whose TABLES-entry label == `label`, by AST.

    Does not import bootstrap_missing (importing it posts DDL to the live bus).
    """
    tree = ast.parse(bootstrap_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TABLES" for t in node.targets
        ):
            if not isinstance(node.value, (ast.List, ast.Tuple)):
                continue
            for elt in node.value.elts:
                if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                    sql_node, label_node = elt.elts
                    if (
                        isinstance(label_node, ast.Constant)
                        and label_node.value == label
                        and isinstance(sql_node, ast.Constant)
                    ):
                        return sql_node.value
    return None


def _cycle_calls(src, callee):
    """True iff a function named 'cycle' in `src` contains a call to `callee`()."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "cycle":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    fn = sub.func
                    if isinstance(fn, ast.Name) and fn.id == callee:
                        return True
                    if isinstance(fn, ast.Attribute) and fn.attr == callee:
                        return True
    return False


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    # register BEFORE exec_module so @dataclass can resolve cls.__module__ (it reads
    # sys.modules[cls.__module__].__dict__); otherwise exec raises AttributeError.
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run(tree):
    fails = []

    # ---- cure 1: the table definition ---------------------------------------
    bootstrap_path = os.path.join(tree, "bootstrap_missing.py")
    bootstrap_src = ""
    table_sql = None
    if not os.path.exists(bootstrap_path):
        fails.append(f"bootstrap_missing.py not found in {tree}")
    else:
        bootstrap_src = open(bootstrap_path, encoding="utf-8-sig").read()
        if "seq_discovery_candidate_id" not in bootstrap_src:
            fails.append("bootstrap_missing.py does not create seq_discovery_candidate_id")
        table_sql = _extract_table_sql(bootstrap_src, "mcp_discovery_candidates")
        if table_sql is None:
            fails.append("bootstrap_missing.py has no mcp_discovery_candidates TABLES entry")
        else:
            missing = CONTRACT_COLUMNS - set(_column_names(table_sql))
            if missing:
                fails.append(f"mcp_discovery_candidates DDL missing columns: {sorted(missing)}")
            if "nextval(" not in table_sql:
                fails.append("mcp_discovery_candidates id has no nextval default")

    # execute the DDL for real and exercise the promoter's access pattern
    if table_sql is not None:
        fails += _exercise_ddl(table_sql)

    # ---- cure 2: the spool drain is wired -----------------------------------
    for pag in PAGINATORS:
        p = os.path.join(tree, pag)
        if not os.path.exists(p):
            fails.append(f"{pag} not found in {tree}")
            continue
        src = open(p, encoding="utf-8-sig").read()
        if "replay_spool" not in src:
            fails.append(f"{pag} never references bus_write_guard.replay_spool")
        if not _cycle_calls(src, "drain_spool"):
            fails.append(f"{pag} cycle() does not call drain_spool()")

    # behavioural: replay is a no-op while ABSENT, drains once PRESENT
    guard_path = os.path.join(tree, "bus_write_guard.py")
    if os.path.exists(guard_path):
        fails += _exercise_replay(guard_path)
    else:
        fails.append("bus_write_guard.py not found (prerequisite #6167)")

    return fails


def _column_names(table_sql):
    """Column names from a CREATE TABLE body -- the leading token of each line that is
    not a CREATE/closing-paren/constraint line."""
    names = []
    body = table_sql.split("(", 1)[1] if "(" in table_sql else table_sql
    for raw in body.splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.startswith(")"):
            continue
        token = line.split()[0]
        if token.upper() in ("CREATE", "PRIMARY", "UNIQUE", "FOREIGN", "CONSTRAINT", "CHECK"):
            continue
        names.append(token)
    return names


def _exercise_ddl(table_sql):
    fails = []
    try:
        import duckdb
    except Exception as e:  # pragma: no cover
        return [f"duckdb unavailable, DDL not executed: {e!r}"]
    try:
        con = duckdb.connect(":memory:")
        con.execute("CREATE SEQUENCE IF NOT EXISTS seq_discovery_candidate_id START 1")
        con.execute(table_sql)
        # a paginator-shaped row: no id, promoted omitted
        con.execute(
            "INSERT INTO mcp_discovery_candidates "
            "(candidate_name, candidate_url, candidate_description, "
            " discovered_in_directory, discovered_status, last_seen) "
            "VALUES ('o/r', 'https://github.com/o/r', 'd', 'github_topic', 'active', now())"
        )
        row = con.execute(
            "SELECT id, promoted FROM mcp_discovery_candidates "
            "WHERE discovered_in_directory='github_topic' LIMIT 1"
        ).fetchone()
        if row is None or row[0] is None:
            fails.append("insert without id did not auto-assign a surrogate id")
        if row is not None and row[1] not in (False, 0):
            fails.append(f"promoted did not default to FALSE (got {row[1]!r})")
        # the promoter's own access: SELECT by funnel filter, then UPDATE ... WHERE id
        cid = row[0] if row else None
        con.execute(
            "SELECT id, candidate_name, candidate_url, candidate_description "
            "FROM mcp_discovery_candidates "
            "WHERE (promoted IS FALSE OR promoted IS NULL) "
            "AND discovered_in_directory='github_topic' LIMIT 50"
        ).fetchall()
        con.execute(
            f"UPDATE mcp_discovery_candidates SET promoted=TRUE, reviewed_at=now() WHERE id={cid}"
        )
        still = con.execute(
            "SELECT count(*) FROM mcp_discovery_candidates WHERE promoted IS FALSE"
        ).fetchone()[0]
        if still != 0:
            fails.append("promoter UPDATE ... WHERE id did not mark the row promoted")
        con.close()
    except Exception as e:
        fails.append(f"DDL execution/exercise raised: {e!r}")
    return fails


def _exercise_replay(guard_path):
    fails = []
    try:
        g = _load_module(guard_path, "bus_write_guard_c194")
    except Exception as e:
        return [f"could not import bus_write_guard: {e!r}"]

    table = "mcp_discovery_candidates"
    spool_dir = tempfile.mkdtemp(prefix="c194_spool_")
    posted = []

    def poster(write_url, tbl, row, timeout):
        posted.append(row)
        return True

    def q_absent(query_url, sql, timeout):
        return {"rows": []}

    def q_present(query_url, sql, timeout):
        return {"rows": [{"table_name": table}]}

    try:
        g.spool_rows(table, [{"candidate_name": "o/r", "candidate_url": "u"}], spool_dir, "ABSENT")
        if g.spool_depth(table, spool_dir=spool_dir) != 1:
            fails.append("spool_rows did not persist the row")

        # ABSENT: replay must NOT post and must NOT empty the spool
        g.reset_cache()
        r1 = g.replay_spool(
            table, write_url="http://bus/write", query_url="http://bus/query",
            spool_dir=spool_dir, querier=q_absent, poster=poster,
        )
        if r1.posted != 0:
            fails.append(f"replay posted {r1.posted} while target ABSENT (must be 0)")
        if g.spool_depth(table, spool_dir=spool_dir) != 1:
            fails.append("replay drained the spool while target ABSENT (data would be lost)")

        # PRESENT: replay drains to the poster and empties the spool
        g.reset_cache()
        r2 = g.replay_spool(
            table, write_url="http://bus/write", query_url="http://bus/query",
            spool_dir=spool_dir, querier=q_present, poster=poster,
        )
        if r2.posted != 1:
            fails.append(f"replay posted {r2.posted} while target PRESENT (expected 1)")
        if g.spool_depth(table, spool_dir=spool_dir) != 0:
            fails.append("replay left rows in the spool after a PRESENT drain")
        if len(posted) != 1:
            fails.append(f"poster received {len(posted)} rows (expected exactly 1)")
    except Exception as e:
        fails.append(f"replay exercise raised: {e!r}")
    return fails


# ---- pytest entry points (check this repo) ----------------------------------
def test_discovery_candidates_bootstrap_and_drain():
    fails = run(_repo_root())
    assert not fails, "cycle-0194 controls RED:\n" + "\n".join(f"  - {f}" for f in fails)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tree", nargs="?", default=_repo_root())
    args = ap.parse_args()
    fails = run(args.tree)
    if fails:
        print(f"RED ({len(fails)}) against {args.tree}:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print(f"GREEN: all cycle-0194 controls pass against {args.tree}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
