"""FU-220: the package __init__ that every liveness contract imports first.

WHY THIS FILE EXISTS
--------------------
tools/promote_staged_to_active.py runs a staged service's acceptance test as:

    python -m services.staged.<name>.contract

`-m` imports the PARENT PACKAGES before it runs the module. So
`services/__init__.py`, `services/staged/__init__.py` and
`services/active/__init__.py` sit upstream of every contract that will ever be
run, for all 262 staged services at once. A single NameError in one of them
takes out the entire promotion gate, not one service.

That is exactly what was on main until 2026-08-01. `services/staged/__init__.py`
declared `def get_signal_scores(mesh_id: str, session: Optional[Session] = None)`
with `Session` never imported. Annotations there are evaluated at def-time, so
`import services.staged` raised `NameError: name 'Session' is not defined` --
on every Python version, deterministically, for anyone who tried.

WHY NOBODY SAW IT, WHICH IS THE PART WORTH KEEPING
--------------------------------------------------
It was behind another wall. The promoter only reaches the contract `if not
reasons`, and until FU-217 every candidate already carried the "import_path is
carried by no Dockerfile COPY directive" reason. So the contract never ran, and
the promoter's report showed `contract_ok: 0` AND `contract FAILED: 0` for six
consecutive days. FU-217 read that correctly at the time: it is zero
MEASUREMENT, not zero failures (HARNESS_DOCTRINE R3/R6 -- a bucket at zero must
prove the check RAN, and unknown is not zero). This test is what that reading
was worth: the moment the first wall came down, the second one was already
there and measurable.

The assertion is deliberately about IMPORTABILITY and nothing else. It does not
lint these files, does not check their contents, and adds no gate to promotion
(R7: recovery over restriction). It answers one question -- can the interpreter
load the packages every contract depends on -- which is the question that was
silently answered "no".
"""

import importlib
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

PACKAGES = ["services", "services.staged", "services.active"]


def _import_in_a_clean_interpreter(module):
    """Import in a SUBPROCESS, not in this one.

    pytest collects test modules from tests/, and an earlier test in the same
    session may already have imported these packages -- sys.modules would then
    serve a cached module and this test would pass without importing anything.
    A check that can be satisfied by a cache is not a check.
    """
    return subprocess.run(
        [sys.executable, "-c", "import %s" % module],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_every_service_package_imports_cleanly():
    """The regression guard. Names the module AND the interpreter error."""
    broken = []
    for module in PACKAGES:
        proc = _import_in_a_clean_interpreter(module)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            broken.append("%s -> %s" % (module, tail[-1] if tail else "rc=%d" % proc.returncode))

    assert not broken, (
        "service package(s) cannot be imported. `python -m "
        "services.staged.<name>.contract` imports the parent packages FIRST, so "
        "this breaks the liveness contract of EVERY staged service at once "
        "(FU-220):\n  " + "\n  ".join(broken)
    )


def test_no_service_package_annotation_references_an_undefined_name():
    """The specific shape of the FU-220 defect, caught at the point of failure.

    The test above proves the packages import. This one proves the reason they
    import is that their annotations RESOLVE, rather than that some future
    `from __future__ import annotations` has made every annotation a lazy
    string -- which would silence this class rather than fix it, and would let
    the same undefined name reach a caller that evaluates type hints
    (FastAPI/pydantic do exactly that on a router signature).
    """
    import typing

    for module in PACKAGES:
        mod = importlib.import_module(module)
        for name in dir(mod):
            obj = getattr(mod, name)
            if not callable(obj) or getattr(obj, "__module__", None) != module:
                continue
            try:
                typing.get_type_hints(obj)
            except NameError as exc:
                raise AssertionError(
                    "%s.%s has an annotation naming something that does not "
                    "exist: %s. This is the FU-220 shape -- it will raise the "
                    "moment anything evaluates the signature." % (module, name, exc)
                )


# --------------------------------------------------------------------------
# NEGATIVE CONTROL -- an assertion never seen RED is unproven (R4).
# --------------------------------------------------------------------------


def test_control_the_importer_reports_a_genuinely_broken_module_as_broken():
    """If _import_in_a_clean_interpreter cannot fail, the test above is a placebo.

    Uses a module name that certainly does not exist rather than writing a
    broken file into services/staged/ -- a test that leaves debris in the tree
    the promoter scans is a worse problem than the one it checks.

    DELIBERATELY A TOP-LEVEL NAME, not `services.staged.__no_such__`. When this
    control was first written it targeted a submodule of services.staged, and
    running it against the pre-fix tree made it fail with `NameError: Session`
    -- i.e. the control reported the defect under test instead of reporting on
    its own mechanism, and its failure message pointed at the wrong thing. A
    control must be independent of the subject it controls for.
    """
    proc = _import_in_a_clean_interpreter("__fu220_no_such_module_anywhere__")
    assert proc.returncode != 0, (
        "importing a nonexistent module succeeded -- the importer used by the "
        "guard above cannot detect a failure and proves nothing"
    )
    assert "ModuleNotFoundError" in (proc.stderr or ""), proc.stderr


def test_control_get_type_hints_actually_raises_on_an_undefined_annotation():
    """The exact mechanism of the FU-220 defect, reproduced in isolation.

    Proves the second test's detector works, without depending on the repo
    still containing an instance of the defect (which it must not).
    """
    import typing

    namespace = {}
    exec(
        "from typing import Optional\n"
        "def f(session: Optional['NotDefinedAnywhere'] = None):\n"
        "    return session\n",
        namespace,
    )
    try:
        typing.get_type_hints(namespace["f"])
    except NameError:
        return
    raise AssertionError(
        "get_type_hints did not raise on an undefined annotation -- the "
        "detector in test_no_service_package_annotation_references_an_undefined_name "
        "cannot go RED and is a placebo"
    )
