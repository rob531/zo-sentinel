"""SQL comments must not produce referents.

WHY THIS FILE EXISTS
    `community` and `graph_table` sat on the phantom-table list from the #4032
    census through #4070's quarantine and into issue #4080, where they were
    listed as two of "18 phantom table names" needing a real fix in a
    load-bearing file. Neither is a table. Both are extractor artefacts.

    Both come from tools/build_app_graph.py:118 -- one SQL string, from two
    lines inside it that are SQL COMMENTS documenting the optional DuckPGQ
    layer:

        --   INSTALL duckpgq FROM community; LOAD duckpgq;
        --   -- FROM GRAPH_TABLE (app

    TABLE_REF matched `FROM community` and `FROM GRAPH_TABLE` and reported both
    as tables existing on no plane. This is the same class of bug as the 408
    prose-docstring false positives SQL_STMT was anchored to remove; comments
    are prose that happens to live inside a SQL literal.

    It stops being a reporting nuisance the moment the TABLES check is armed.
    referent-verify is a REQUIRED status check, so a false MISSING blocks a
    merge on a referent nobody ever named -- which is how a correct gate earns
    itself an off switch. These tests hold the fix in place.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _rv():
    spec = importlib.util.spec_from_file_location(
        "referent_verify", ROOT / "tools" / "referent_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RV = _rv()


def _tables(sql):
    return RV.extract_refs(sql)[0]


# --- the two names that were on the #4080 list -----------------------------

def test_duckpgq_install_comment_is_not_a_table():
    sql = ("SELECT n.area FROM app_node n\n"
           "--   INSTALL duckpgq FROM community; LOAD duckpgq;\n")
    assert _tables(sql) == {"app_node"}


def test_graph_table_match_comment_is_not_a_table():
    sql = ("SELECT n.area FROM app_node n\n"
           "--   -- FROM GRAPH_TABLE (app\n"
           "--   --   MATCH (u:app_node)-[:reads]->(t:app_node)\n")
    assert _tables(sql) == {"app_node"}


def test_the_real_build_app_graph_statement_names_only_its_own_tables():
    """The literal that produced both names, end to end."""
    src = (ROOT / "tools" / "build_app_graph.py").read_text(encoding="utf-8")
    import ast
    tree = ast.parse(src)
    found = set()
    for sql, _ln in RV._iter_sql_strings(tree):
        found |= _tables(sql)
    assert "community" not in found
    assert "graph_table" not in found
    # ...and the statement's real referents survive
    assert {"app_node", "app_edge"} <= found


# --- the general rule ------------------------------------------------------

def test_block_comment_is_stripped():
    assert _tables("SELECT 1 FROM app_node /* FROM secret_table */") == {"app_node"}


def test_trailing_line_comment_is_stripped():
    assert _tables("SELECT 1 FROM app_node  -- FROM ghost_tbl") == {"app_node"}


def test_unterminated_block_comment_is_stripped_to_end():
    assert _tables("SELECT 1 FROM app_node /* FROM ghost_tbl") == {"app_node"}


def test_comment_stripping_is_quote_aware():
    """A `--` inside a string literal is data, not the start of a comment.

    Blanking from the first `--` regardless of quoting would silently drop the
    rest of the statement, so a real table named after it would stop being
    checked -- a false PASS, which is the worse direction.
    """
    sql = ("SELECT 1 FROM app_node WHERE note = 'x -- y' "
           "AND id IN (SELECT id FROM mcp_server_registry)")
    assert "mcp_server_registry" in _tables(sql)


def test_offsets_are_preserved_so_ctes_still_resolve():
    sql = ("WITH churned AS (  -- FROM ghost\n"
           "  SELECT server_id FROM mcp_server_registry\n"
           ")\n"
           "SELECT * FROM churned")
    t = _tables(sql)
    assert "ghost" not in t
    assert "churned" not in t          # a CTE is not a missing table
    assert "mcp_server_registry" in t


def test_commented_out_create_does_not_register_as_code_created():
    """The other direction of the same bug.

    If a commented-out CREATE counted, a genuinely missing table would read as
    code-created and pass -- a false PASS on an armed check.
    """
    stripped = RV.strip_sql_comments("-- CREATE TABLE ghost AS SELECT 1")
    assert not RV.CREATED_TABLE.search(stripped)
    live = RV.strip_sql_comments("CREATE TABLE real_stage AS SELECT 1")
    m = RV.CREATED_TABLE.search(live)
    assert m and m.group(1) == "real_stage"
