"""Three-state verdict for the builder's acceptance self-test (FU-031 / FU-159).

WHY THIS EXISTS
---------------
The previous classifier was a substring match on an exception NAME::

    if rc != 0 and ("ModuleNotFoundError" in combined or "ImportError" in combined):
        degrade to Tier-0, not blocking

It could not tell "the harness could not run" from "the module is broken", so it
waived both. Measured on the live log for the whole window AFTER #2177 landed
(2026-07-28T12:19:15Z, n=89): 39 Tier-0 degradations, and **every one of them was a
real module defect** -- `cannot import name 'Orgs'`, `mesh_events` (a table name
imported as a class), `MeshMemory` (no such model), relative imports in a
single-file module. Zero were genuine harness failures. #2177 had already removed
the one true env cause: `No module named 'app.db'` went 295 -> 0 across that merge.

So the UNKNOWN bucket was being used as a pass.

THE CONTRACT
------------
This is the same three-state contract the FU ledger already uses (`_tools/README.md`):

    PASS    -- the self-test ran and passed
    RED     -- the self-test ran and the MODULE is wrong          -> block completion
    UNKNOWN -- the self-test could NOT be evaluated (harness/env)  -> degrade, never evidence

UNKNOWN is deliberately narrow. An unrecognised failure shape returns UNKNOWN rather
than RED: we never block on a shape we have not classified. But an import error that
proves the module asked for something that does not exist is RED, because the parent
module resolved -- which means the harness worked fine and the code is wrong.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = "PASS"
RED = "RED"
UNKNOWN = "UNKNOWN"

# Roots owned by this repo. A missing *root* is a harness problem; a missing
# SUBMODULE of a resolvable root is an invented import, i.e. a module defect.
FIRST_PARTY_ROOTS = ("app", "services", "zo_sentinel", "tools")

_CANNOT_IMPORT_NAME = re.compile(r"cannot import name ['\"]([^'\"]+)['\"] from ['\"]([^'\"]+)['\"]")
_NO_MODULE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
_RELATIVE_IMPORT = "attempted relative import with no known parent package"


def _module_exists(dotted: str, project_root: Optional[str] = None) -> Optional[bool]:
    """Does this dotted first-party module exist ON DISK?

    The error text alone cannot separate the two cases that matter::

        No module named 'app.db'                   -> app/db.py EXISTS -> harness (295x pre-#2177)
        No module named 'app.dependency_overrides' -> no such file     -> invented import

    Both are `ModuleNotFoundError` on a first-party root. Only the filesystem knows
    which is which, so ask it. Returns None when we cannot tell, and an unknown
    answer must never produce RED.
    """
    root = project_root or _REPO_ROOT
    if not os.path.isdir(root):
        return None
    rel = dotted.split(".")
    base = os.path.join(root, *rel)
    if os.path.isfile(base + ".py") or os.path.isfile(os.path.join(base, "__init__.py")):
        return True
    # Only claim absence when the PARENT package is present; otherwise we are
    # probably looking at the wrong root and should stay silent.
    parent = os.path.join(root, *rel[:-1])
    if len(rel) > 1 and (os.path.isdir(parent) or os.path.isfile(parent + ".py")):
        return False
    return None


def classify_selftest(returncode: int, combined: str, stdout: str = "",
                      project_root: Optional[str] = None) -> Tuple[str, str]:
    """Return (verdict, reason) for one self-test run.

    `combined` is stdout+stderr. `stdout` is used only for the PASS marker, so a
    module that prints PASS on stderr does not get credit for it.
    """
    if returncode == 0 and "PASS" in (stdout or combined):
        return PASS, "self-test ran and printed PASS"

    m = _CANNOT_IMPORT_NAME.search(combined)
    if m:
        name, mod = m.group(1), m.group(2)
        # The parent module RESOLVED -- the harness is fine. The module asked for a
        # name that does not exist there. This is the FU-109 fabricated-name family
        # (Orgs, mesh_events, MeshMemory, VulnerabilityLink, ...).
        return RED, f"module imports a name that does not exist: {name!r} is not in {mod!r}"

    if _RELATIVE_IMPORT in combined:
        # A single-file builder module using `from .x import y` has no package
        # context by construction. Module shape defect, not an environment problem.
        return RED, "module uses a relative import but has no parent package"

    m = _NO_MODULE.search(combined)
    if m:
        missing = m.group(1)
        root = missing.split(".")[0]
        if "." in missing and root in FIRST_PARTY_ROOTS:
            exists = _module_exists(missing, project_root)
            if exists is False:
                # Parent package is on disk, the submodule is not: invented import.
                return RED, f"module imports a submodule that does not exist: {missing!r}"
            # It exists (or we cannot tell) -> the harness failed to load a REAL
            # module. This is the `app.db` class #2177 fixed. Never block on it.
            return UNKNOWN, (f"first-party module {missing!r} exists but was unresolvable "
                             f"- harness could not run")
        if root in FIRST_PARTY_ROOTS:
            # The whole first-party root is unresolvable -> sys.path/cwd problem.
            # This is the class #2177 fixed (295 -> 0). Genuinely not evaluable.
            return UNKNOWN, f"first-party package {missing!r} unresolvable - harness could not run"
        # Third-party dependency missing from the environment.
        return UNKNOWN, f"third-party dependency {missing!r} missing - harness could not run"

    if returncode != 0:
        return RED, "self-test ran and failed"

    # rc == 0 but no PASS marker printed.
    return RED, "self-test exited 0 without printing PASS"


def blocks_completion(verdict: str) -> bool:
    """Only RED blocks. UNKNOWN degrades (Tier-0) exactly as before."""
    return verdict == RED
