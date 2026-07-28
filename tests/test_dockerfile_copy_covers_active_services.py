"""FU-102 guard: every active service must actually EXIST inside the prod image.

Root cause this prevents (live on prod v64, 2026-07-25 -> 2026-07-27):
app/main.py mounts every service listed under services/active/ at build time, but
the Dockerfile COPY-list is hand-maintained. When a builder lands a new service
and nobody updates the COPY-list, the module is simply absent from the image and
prod raises ModuleNotFoundError at mount time -- /spine/health goes ok:false with
a non-empty failures[] while CI stays green, because CI runs against the repo
tree (where the file exists), not the image.

METHOD CHANGE 2026-07-28: this gate used to ask "does <dir-name>.py exist at the
repo root and is it COPY'd?". That is a PROXY. The spine imports
`[service].import_path` verbatim (tools/generate_spine.py -> SPINE_MOUNTS ->
importlib.import_module), not a directory name. The proxy silently SKIPS every
dotted import_path as "no root module to ship, not a gap" -- which is the exact
shape `services/staged/*` all declare (`services.active.<name>.router`), and
the Dockerfile COPYs no `services/` path at all. The guard for the v64 class was
structurally blind to the next occurrence of the v64 class. It now resolves the
DECLARED import_path and checks it against the paths the Dockerfile really
carries. See tools/image_ship_check.py for the full write-up.

False-positive guard preserved: a services/active/<name>/ dir whose import_path
resolves to no file at all (or which declares none) is not this gate's finding.
Require a real module on disk before demanding a COPY for it.
"""

import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from tools.image_ship_check import (  # noqa: E402
    copy_source_tokens,
    is_copy_covered,
    read_import_path,
    resolve_module_path,
    shippability,
)

DOCKERFILE = os.path.join(REPO_ROOT, "Dockerfile")
ACTIVE_DIR = os.path.join(REPO_ROOT, "services", "active")


def _read_dockerfile():
    with open(DOCKERFILE, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _active_service_names():
    if not os.path.isdir(ACTIVE_DIR):
        return []
    return sorted(
        name
        for name in os.listdir(ACTIVE_DIR)
        if os.path.isdir(os.path.join(ACTIVE_DIR, name))
    )


def _declared_import_path(name):
    """import_path from service.toml, falling back to the dir name.

    The fallback keeps the pre-2026-07-28 behaviour for any service whose
    service.toml is missing or shapeless -- it is not silently exempted.
    """
    ip = read_import_path(os.path.join(ACTIVE_DIR, name, "service.toml"))
    return ip or name


def test_dockerfile_copies_every_active_service_root_module():
    """Every ACTIVE service's declared import_path must be carried by a COPY."""
    dockerfile = _read_dockerfile()

    gaps = []
    for name in _active_service_names():
        import_path = _declared_import_path(name)
        verdict, detail = shippability(REPO_ROOT, import_path, dockerfile)
        if verdict == "NOT_SHIPPED":
            gaps.append("%s: %s" % (name, detail))

    assert not gaps, (
        "services/active entries whose import_path is absent from the image; "
        "these will ModuleNotFoundError on prod at mount time:\n  "
        + "\n  ".join(gaps)
    )


def test_copy_list_has_no_dangling_root_modules():
    """COPY-list entries naming a root .py that no longer exists break the image
    build outright. Cheaper to catch here than at deploy time."""
    dangling = []
    for src in copy_source_tokens(_read_dockerfile()):
        if src.endswith(".py") and "/" not in src and "*" not in src:
            if not os.path.exists(os.path.join(REPO_ROOT, src)):
                dangling.append(src)

    assert not dangling, (
        "Dockerfile COPY-list references root modules that do not exist: "
        + ", ".join(dangling)
    )


def test_every_active_service_declares_a_resolvable_import_path():
    """A service.toml pointing at nothing is invisible to the gate above.

    Without this, a service could dodge the COPY check forever by declaring an
    import_path that resolves to no file -- shippability() would return
    NO_SOURCE and the gap would never be reported. Recorded as a WARNING-shaped
    assertion: it lists them rather than blocking on a class that does not exist
    today, so a future regression is attributed to the right cause.
    """
    unresolvable = []
    for name in _active_service_names():
        import_path = _declared_import_path(name)
        if resolve_module_path(REPO_ROOT, import_path) is None:
            unresolvable.append("%s -> %s" % (name, import_path))

    assert not unresolvable, (
        "services/active entries whose import_path resolves to no file on disk; "
        "the spine will ModuleNotFoundError on these regardless of the COPY-list: "
        + ", ".join(unresolvable)
    )


# --------------------------------------------------------------------------
# NEGATIVE CONTROLS -- every assertion above is proven capable of going RED.
# Two of eight assertions in #2068 were placebos that survived a careful
# reading; controls are standard here, not optional.
# --------------------------------------------------------------------------

_FAKE_DOCKERFILE = """
FROM python:3.11-slim
COPY app/requirements.txt /srv/app/requirements.txt
COPY app /srv/app
COPY alpha.py beta.py /srv/
COPY migrations /srv/migrations
"""


def test_control_root_module_in_copy_list_is_shipped():
    assert is_copy_covered("alpha.py", copy_source_tokens(_FAKE_DOCKERFILE))


def test_control_dotted_path_under_a_copied_package_is_shipped():
    """The media_assets shape: app.routers.media_assets under `COPY app`."""
    assert is_copy_covered(
        "app/routers/media_assets.py", copy_source_tokens(_FAKE_DOCKERFILE)
    )


def test_control_dotted_path_under_an_uncopied_tree_is_not_shipped():
    """THE REGRESSION THIS FILE EXISTS FOR: services/ is never COPY'd, so a
    promoted staged service imported as services.active.<n>.router cannot load."""
    assert not is_copy_covered(
        "services/active/entity_report/router.py",
        copy_source_tokens(_FAKE_DOCKERFILE),
    )


def test_control_prefix_collision_is_not_mistaken_for_coverage():
    """`COPY app` must not be read as covering `application/x.py`. Ancestor
    matching is per path SEGMENT, not string prefix."""
    assert not is_copy_covered(
        "application/x.py", copy_source_tokens(_FAKE_DOCKERFILE)
    )


def test_control_line_continuations_are_joined_before_parsing():
    """A multi-line COPY block must not parse as bogus one-token directives --
    the real Dockerfile has one, and missing it would make the gate blind to
    16 modules at once."""
    text = "COPY one.py two.py \\\n    three.py /srv/\n"
    assert set(copy_source_tokens(text)) == {"one.py", "two.py", "three.py"}


def test_control_the_real_dockerfile_copies_no_services_tree():
    """Pins the fact the gate above depends on. If someone adds
    `COPY services /srv/services`, this fails and forces a deliberate re-read of
    whether staged code should now be shipping into the prod image."""
    tokens = set(copy_source_tokens(_read_dockerfile()))
    shipped_services_trees = sorted(
        t for t in tokens if t == "services" or t.startswith("services/")
    )
    assert not shipped_services_trees, (
        "the Dockerfile now COPYs a services/ tree: %s -- re-read "
        "tools/image_ship_check.py before accepting this" % shipped_services_trees
    )
