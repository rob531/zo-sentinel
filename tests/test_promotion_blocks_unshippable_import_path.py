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


def test_staged_style_import_path_would_not_be_shipped():
    """RED-able control on the real Dockerfile: this is the live hazard."""
    assert not would_be_shipped(
        "services.active.entity_report.router", _dockerfile()
    ), (
        "services/ is now carried into the image -- if that is deliberate, the "
        "promotion hazard is resolved and this test should be re-read, not deleted"
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
