"""The negative control for tools/stranded_review.py, run by CI.

Every verdict this grader can emit is asserted together with its COMPLEMENT.
The two that matter are not the happy paths:

  * an ABSENT catalog must grade UNKNOWN, never CLEAN -- a CLEAN that means
    "the check never ran" is the majority class of this repo's ledger;
  * an EMPTY catalog with no reason must grade PHANTOM -- which is why the
    reason has to be propagated rather than swallowed into `{}`. Swallow it
    and a missing catalog reports as 46 phantom files, a false FAIL, and the
    instrument gets switched off.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sr = _load(TOOLS / "stranded_review.py", "stranded_review_under_test")
rv = _load(TOOLS / "referent_verify.py", "referent_verify_for_sr_test")

CATALOG, _META, UNKNOWN_REASON = rv.load_catalog()


def _sql(table: str) -> str:
    return 'QUERY = "SELECT id FROM %s WHERE x = 1"\n' % table


@pytest.fixture(scope="module")
def real_table() -> str:
    if UNKNOWN_REASON or not CATALOG:
        pytest.skip("catalog unavailable (%s) -- UNKNOWN, not a pass"
                    % UNKNOWN_REASON)
    return sorted(str(t) for t in CATALOG)[0]


def test_phantom_table_is_phantom():
    verdict, _named, missing, _d = sr.grade_source(
        _sql("no_such_table_c175_negative_control"), rv, CATALOG, UNKNOWN_REASON)
    assert verdict == "PHANTOM"
    assert "no_such_table_c175_negative_control" in missing


def test_real_table_is_clean(real_table):
    verdict, named, missing, _d = sr.grade_source(
        _sql(real_table), rv, CATALOG, UNKNOWN_REASON)
    assert verdict == "CLEAN", (named, missing)
    assert missing == []


def test_unparseable_is_broken():
    verdict, _n, _m, detail = sr.grade_source("def f(:\n", rv, CATALOG,
                                              UNKNOWN_REASON)
    assert verdict == "BROKEN"
    assert "SyntaxError" in detail


def test_absent_catalog_is_unknown_not_clean(real_table):
    """R6. The pole the whole file exists for."""
    verdict, _n, _m, detail = sr.grade_source(
        _sql(real_table), rv, {}, "test: catalog withheld")
    assert verdict == "UNKNOWN"
    assert verdict != "CLEAN"
    assert "nothing was checked" in detail


def test_empty_catalog_without_reason_is_phantom(real_table):
    """Why unknown_reason must be propagated, not swallowed into {}."""
    verdict, _n, _m, _d = sr.grade_source(_sql(real_table), rv, {}, None)
    assert verdict == "PHANTOM"


def test_create_table_in_the_same_file_resolves():
    src = ('DDL = "CREATE TABLE c175_local_only (id int)"\n'
           'Q = "SELECT id FROM c175_local_only"\n')
    verdict, _n, _m, _d = sr.grade_source(src, rv, CATALOG, UNKNOWN_REASON)
    assert verdict == "CLEAN"


def test_self_test_entrypoint_passes():
    assert sr.self_test(rv, CATALOG, UNKNOWN_REASON) == 0


def test_self_test_refuses_without_a_catalog():
    """UNKNOWN is rc=2 from the control too -- never a silent pass."""
    assert sr.self_test(rv, CATALOG, "withheld") == 2
    assert sr.self_test(rv, {}, None) == 2


def test_main_refuses_a_tools_dir_whose_predicate_predates_5922(tmp_path):
    """A copy without landed_state() grades presence with is_file() -- the
    #4079 defect. Importing it must be refused, not worked around."""
    stub = tmp_path / "tools"
    stub.mkdir()
    (stub / "requeue_quarantined.py").write_text(
        "def already_back(repo, c):\n    return (repo / c).is_file()\n",
        encoding="utf-8")
    (stub / "referent_verify.py").write_text(
        (TOOLS / "referent_verify.py").read_text(encoding="utf-8"),
        encoding="utf-8")
    rc = sr.main(["--repo", str(ROOT), "--tools-from", str(stub)])
    assert rc == 2


def test_main_report_only_on_the_live_repo_is_rc0():
    """Report-only must never block, even with PHANTOM rows present."""
    rc = sr.main(["--repo", str(ROOT)])
    assert rc == 0
