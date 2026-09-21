"""Make `tools` a REGULAR package so a dependency cannot shadow it.

WHY THIS FILE EXISTS (2026-08-05, daily-chairman-review)
--------------------------------------------------------
Until now `tools/` had no __init__.py, which made it a NAMESPACE package. A
namespace portion is provisional: the import system records it and KEEPS
SCANNING the rest of sys.path, and the first REGULAR package with the same name
wins outright, discarding the namespace portions -- even when the repo root is
sitting at sys.path[0].

Mako 1.4.0 ships a top-level `tools/__init__.py` (containing only its own
toxnox.py / warn_tox.py dev helpers) into site-packages. Mako is a transitive
dependency of alembic, which tests/ci/requirements-ci.txt installs unpinned. The
release landed between 2026-08-04T12:21Z and 2026-08-04T20:35Z, and from the
first CI run after it:

    ImportError while importing test module
      '.../tests/test_dockerfile_copy_covers_active_services.py'
    E   ModuleNotFoundError: No module named 'tools.image_ship_check'
    !!!! Interrupted: 1 error during collection !!!!
    ##[error]Process completed with exit code 2.

Collection was INTERRUPTED, so the pytest job ran ZERO tests -- on main and on
every open PR -- while reporting FAILURE. Roughly 40 builder PRs were blocked by
a red check that had stopped saying anything about their code. Nothing in the
repo changed; only an unpinned transitive dependency did.

WHY A PIN IS NOT THE FIX
------------------------
Pinning Mako<1.4.0 addresses this occurrence. Being shadowable by any dependency
that happens to ship a top-level `tools` is the defect. An __init__.py at
sys.path[0] wins against a site-packages regular package, so this closes the
class rather than the instance -- recovery over restriction (HARNESS_DOCTRINE
R7), and no new required check.

VERIFIED, BOTH DIRECTIONS (R4 -- an assertion never observed RED is UNPROVEN):
_probes/mako_tools_shadow_control.py builds a repo tree against a venv with Mako
1.4.0 installed and asserts BOTH halves:
  - WITHOUT this file: rc=1, "No module named 'tools.image_ship_check'"
    (reproduces the CI error string exactly)
  - WITH this file:    rc=0, import succeeds
A fix observed only succeeding has not been shown to be the cause of anything.
"""
