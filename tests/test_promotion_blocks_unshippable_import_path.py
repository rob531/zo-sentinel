"""The guard belongs in the ACTOR, not only in the watcher.

tests/test_dockerfile_copy_covers_active_services.py catches an unshippable
service AFTER it is already in services/active -- i.e. after the promoter moved
it, after the spine regenerated, on a PR someone now has to revert. The scar
`fix_landed_in_the_watcher_not_the_actor` (#2068) is exactly this shape: the
detector was hardened while the thing that performs the action kept its old
behaviour all day.

tools/promote_staged_to_active.py is the actor. It MOVES services/staged/<name>/
to services/active/<name>/ without rewriting [service].import_path, and
tools/generate_spine.py then emits that import_path verbatim for
importlib.import_module. Every staged service today declares
`services.active.<name>.router`, and the Dockerfile COPYs no services/ tree, so
each promotion is a guaranteed prod ModuleNotFoundError -- the v64 failure, at
the scale of the staged backlog.

So the promoter must HOLD on it. These tests prove that it does, and prove the
check is capable of both verdicts.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from tools.image_ship_check import would_be_shipped  # noqa: E402

DOCKERFILE = os.path.join(REPO_ROOT, "Dockerfile")


def _dockerfile():
    with open(DOCKERFILE, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_promoter_source_consults_the_shippability_check():
    """A check that exists but is never called is not a guard (#1784 was merged
    and uncalled for weeks). Assert the actor actually reads it."""
    src_path = os.path.join(REPO_ROOT, "tools", "promote_staged_to_active.py")
    with open(src_path, encoding="utf-8", errors="replace") as fh:
        src = fh.read()
    assert "would_be_shipped" in src, (
        "tools/promote_staged_to_active.py no longer consults would_be_shipped; "
        "the promotion path can ship an unimportable service again"
    )


def test_staged_style_import_path_is_now_shippable():
    """FU-217, 2026-08-01: the hazard was resolved, so this test was RE-READ.

    Its previous form asserted `not would_be_shipped(...)` and carried its own
    instruction for this moment: *"services/ is now carried into the image -- if
    that is deliberate, the promotion hazard is resolved and this test should be
    re-read, not deleted"*. It was deliberate. `COPY services/active
    /srv/services/active` landed with FU-217 because the hold it produced was
    total -- 267 of 300 promoter candidates, with no COPY-list entry available
    to add a service to, since there was no COPY-list for services at all.

    What the test proves has therefore flipped, and the thing it must NOT lose
    is the ability to go RED: see the companion control below, which keeps a
    genuinely uncovered tree in the assertion set. A check that can only
    succeed is not a check.
    """
    assert would_be_shipped("services.active.entity_report.router", _dockerfile()), (
        "services/active is no longer carried by any Dockerfile COPY directive "
        "-- every promoted service is a ModuleNotFoundError at mount and the "
        "promoter will hold the entire staged backlog again (FU-217)"
    )


def test_an_uncopied_tree_is_still_not_shipped():
    """THE NEGATIVE CONTROL for the inversion above, on the REAL Dockerfile.

    FU-217 widened what `would_be_shipped` accepts. The failure mode of any
    such widening is that it stops being able to say no. `tools/` is a real
    directory of real modules that is deliberately NOT in the image, so this
    assertion is answerable, it is RED today under any regression that makes
    coverage prefix-shaped or unconditional, and it does not depend on a
    fixture that could drift away from the shipped Dockerfile.
    """
    assert not would_be_shipped("tools.fire_gate", _dockerfile()), (
        "tools/ is now reported as shipped -- would_be_shipped has stopped "
        "being able to return False and no longer gates anything"
    )
    assert not would_be_shipped("services.staged.entity_report.router", _dockerfile()), (
        "services/staged is now reported as shipped -- liveness-ungated code "
        "is reaching the prod image (see the two-sided assertion in "
        "tests/test_dockerfile_copy_covers_active_services.py)"
    )


def test_root_module_import_path_would_be_shipped():
    """The shape every currently-active service uses must still pass, or the
    check would block every promotion including correct ones."""
    assert would_be_shipped("verdict_breakdown_api", _dockerfile())


def test_package_import_path_under_app_would_be_shipped():
    """media_assets' shape: app.routers.media_assets under `COPY app /srv/app`."""
    assert would_be_shipped("app.routers.media_assets", _dockerfile())


def test_check_does_not_depend_on_the_module_existing_yet():
    """The whole point: at promotion time the file is still under
    services/staged/, so an existence-based check fires on nothing."""
    assert not os.path.exists(
        os.path.join(REPO_ROOT, "services", "active", "entity_report", "router.py")
    )
    # ...and the check still returns a verdict rather than abstaining.
    assert would_be_shipped("verdict_breakdown_api", _dockerfile()) is True
