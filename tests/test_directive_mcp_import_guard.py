"""FU-158 -- a module that calls sys.exit() AT IMPORT TIME aborts pytest COLLECTION.

`SystemExit` derives from BaseException, so `except Exception:` -- the guard
tests/test_directive_done_dedup.py actually wrote -- CANNOT catch it. pytest
raises INTERNALERROR and the whole session stops: on this tower 178 of the 425
tests CI collects never ran, and the summary line reads fast and clean rather
than red. CI never saw it because the tower-path mkdir in directive_mcp raises
an ordinary Exception there first -- which is exactly why it survived.

Note the shape: tests/test_directive_investigate_loop.py guards the SAME import
with `except (Exception, SystemExit)`. The lesson had already been learned in
one copy and not the other, and the ignorant copy sorts first.

Two assertions, because the two defects are independent:
  1. directive_mcp must not sys.exit() when IMPORTED (as __main__ it still does).
  2. every test module that exec_module()s it must also survive a SystemExit,
     so a re-introduction upstream cannot silently truncate the suite again.
"""
import importlib.util
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
DMCP = REPO / "zo_sentinel" / "mcp_servers" / "directive_mcp.py"
TESTS = REPO / "tests"


def test_importing_directive_mcp_never_raises_systemexit():
    """Import may FAIL (missing mcp SDK, unwritable tower path) -- that is fine and
    catchable. It must never terminate the interpreter, because an importer cannot
    defend against that without `except BaseException`."""
    assert DMCP.is_file(), f"missing {DMCP}"
    spec = importlib.util.spec_from_file_location("dmcp_import_guard_probe", DMCP)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit as exc:  # pragma: no cover - the defect under test
        pytest.fail(
            "directive_mcp raised SystemExit(%r) at IMPORT. pytest cannot catch this "
            "during collection, so the whole session dies with INTERNALERROR and the "
            "remaining tests are silently skipped. Guard the exit behind "
            "`if __name__ == \"__main__\":` and re-raise otherwise." % (exc.code,)
        )
    except Exception:
        pass  # unavailable optional dependency -- catchable, therefore correct


def test_every_test_that_execs_directive_mcp_guards_systemexit():
    """A guard that names only `Exception` is blind to the one failure mode this
    module actually produces on the tower."""
    offenders = []
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name == pathlib.Path(__file__).name:
            continue
        src = path.read_text(encoding="utf-8")
        if "directive_mcp" not in src or "exec_module" not in src:
            continue
        if "SystemExit" not in src and "BaseException" not in src:
            offenders.append(path.name)
    assert not offenders, (
        "these modules exec_module() directive_mcp but their except clause cannot "
        "catch SystemExit, so a module-level sys.exit() aborts COLLECTION for the "
        "entire suite: %s" % ", ".join(offenders)
    )
