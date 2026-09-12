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


# Directories under services/active/ that are not services. __pycache__ is the
# one that matters: it does not exist in a fresh CI checkout, but IS created on
# the tower the moment any earlier gate runs python -- so a check that treats it
# as a service passes in GitHub and fails in the deploy verifier. Caught exactly
# that way on 2026-07-28. Pre-flight must mirror runtime variance, not the
# pristine case.
_NON_SERVICE_DIRS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}


def _is_service_dir(name):
    if name in _NON_SERVICE_DIRS:
        return False
    if name.startswith(".") or name.startswith("__"):
        return False
    return os.path.isdir(os.path.join(ACTIVE_DIR, name))


def _active_service_names():
    if not os.path.isdir(ACTIVE_DIR):
        return []
    return sorted(name for name in os.listdir(ACTIVE_DIR) if _is_service_dir(name))


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


def test_the_real_dockerfile_ships_active_services_and_not_staged():
    """FU-217: this assertion was INVERTED on 2026-08-01, deliberately.

    It used to read `assert not shipped_services_trees` -- a tripwire whose
    stated purpose was to fail if anyone added `COPY services /srv/services`
    and thereby "force a deliberate re-read of whether staged code should now
    be shipping into the prod image". That re-read happened; this is its
    outcome, recorded where the tripwire was rather than only in a commit
    message that nobody reading this file would see.

    THE RE-READ. With no services/ COPY token at any depth, `would_be_shipped`
    was False for every `services.active.<name>.router`, which is what every
    staged service declares. The promoter therefore held 267 of 300 candidates
    (measured 2026-08-01T14:32Z, runtime b1c0d758) and the first autonomous
    staged->active promotion was unreachable by any amount of builder output,
    service.toml repair or contract work. The hazard the tripwire named was
    real; the answer to it is not "never ship services/", it is "ship exactly
    the half that has passed a liveness gate".

    So the tripwire is not deleted and it is not weakened -- it is made
    two-sided and permanent. It now pins BOTH facts the gates above depend on:
    services/active IS carried (or promotion is impossible again) and
    services/staged is NOT (or un-gated code reaches the prod image). Before
    this change the second fact held only as a side effect of the first being
    false; it is now asserted in its own right, which is strictly more than the
    original tripwire proved.
    """
    tokens = set(copy_source_tokens(_read_dockerfile()))
    services_tokens = sorted(
        t for t in tokens if t == "services" or t.startswith("services/")
    )

    assert "services/active" in services_tokens, (
        "no COPY token carries services/active -- every promoted service is "
        "imported as services.active.<name>.router and would "
        "ModuleNotFoundError at mount (FU-217/FU-102). Got: %s" % services_tokens
    )

    staged = sorted(
        t
        for t in services_tokens
        if t == "services"
        or t == "services/staged"
        or t.startswith("services/staged/")
    )
    assert not staged, (
        "the Dockerfile now carries staged (liveness-ungated) service code into "
        "the prod image via %s -- a bare `COPY services` does this too. "
        "Promotion MOVES a dir staged -> active, so shipping staged buys "
        "nothing and puts un-gated modules one import away from a mounted app."
        % staged
    )


def test_control_services_active_coverage_is_segment_matched_not_prefix_matched():
    """The inversion above must not have been bought with a sloppier matcher.

    `services/active` must cover services/active/<n>/router.py and must NOT be
    read as covering a sibling whose name merely starts with the same string.
    This is the negative control for the new COPY token specifically: without
    it, a prefix-matching regression would make the assertion above pass while
    silently claiming coverage of services/staged too.
    """
    tokens = ["services/__init__.py", "services/active"]
    assert is_copy_covered("services/active/entity_report/router.py", tokens)
    assert not is_copy_covered("services/staged/entity_report/router.py", tokens)
    assert not is_copy_covered("services/activex/router.py", tokens)
    assert not is_copy_covered("services/other.py", tokens)


def test_control_cache_dirs_are_not_mistaken_for_services():
    """RED-able control for the defect above: __pycache__ under services/active
    must never be treated as a service, or the gate fails on any machine that
    has run python in the tree -- i.e. every machine except a fresh CI runner."""
    assert not _is_service_dir("__pycache__")
    assert not _is_service_dir(".pytest_cache")


def test_control_a_real_service_dir_is_still_recognised():
    """The exclusion must not be so broad it empties the gate."""
    assert "config_scan_api" in _active_service_names()
    assert len(_active_service_names()) >= 20