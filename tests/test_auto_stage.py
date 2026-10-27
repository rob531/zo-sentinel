"""A builder artifact may edit a service in active/, but may not create one.

`generate_spine.py --strict` is the top blocking failure on autonomous build
PRs. The publisher emits exactly one file, so a new `services/active/<name>/`
arrives with no `service.toml` and --strict fails it as `<name>=NO_TOML`
forever -- no re-run can fix it, which is why those PRs sit open for weeks.

These tests pin the boundary that makes the redirect safe: CREATION moves to
services/staged/ (where service_decomposer already puts built services and
where promote_staged_to_active is the gated path into active/), while an EDIT
of a service that already exists is left alone. Redirecting an edit would fork
a staged copy and leave prod untouched -- worse than the bug being fixed.
"""
import pytest

from zo_sentinel.publisher import auto_stage


@pytest.fixture()
def clone(tmp_path):
    """A clone with one REAL service (it has the manifest) in active/."""
    live = tmp_path / "services" / "active" / "live_svc"
    live.mkdir(parents=True)
    (live / "service.toml").write_text(
        '[service]\nname = "live_svc"\nimport_path = "services.active.live_svc.router"\n'
        'prefix = "/api"\ntag = "live"\n', encoding="utf-8")
    return tmp_path


def test_new_active_service_is_redirected_to_staged(clone):
    """The founding case: risk_axis_time_series=NO_TOML."""
    out, reason = auto_stage.redirect(
        clone, "services/active/risk_axis_time_series/router.py")
    assert out == "services/staged/risk_axis_time_series/router.py"
    assert reason and "NO_TOML" in reason


def test_edit_of_existing_service_is_untouched(clone):
    """A live service is maintained in place. Forking it to staged would leave
    prod unchanged while looking like a successful build."""
    out, reason = auto_stage.redirect(clone, "services/active/live_svc/router.py")
    assert out == "services/active/live_svc/router.py"
    assert reason is None


def test_non_router_files_in_a_new_service_also_redirect(clone):
    """The cohort includes dashboard.html, not just router.py."""
    out, reason = auto_stage.redirect(
        clone, "services/active/cve_analysis_dashboard/dashboard.html")
    assert out.startswith("services/staged/cve_analysis_dashboard/")
    assert reason


@pytest.mark.parametrize("path", [
    "services/staged/foo/router.py",
    "risk_tier_aggregator.py",
    "zo_sentinel/dead_organ_report.py",
    "tools/reachability_deferred.json",
    "app/routers.py",
])
def test_paths_outside_active_are_never_touched(clone, path):
    out, reason = auto_stage.redirect(clone, path)
    assert (out, reason) == (path, None)


def test_a_bare_services_active_path_is_not_mangled(clone):
    out, reason = auto_stage.redirect(clone, "services/active/")
    assert out == "services/active/" and reason is None


def test_manifest_is_what_defines_an_existing_service(clone):
    """A directory with stray files but no manifest is not a service: it is a
    directory a previous build happened to create, and writing more into it
    keeps the PR red."""
    stray = clone / "services" / "active" / "half_built"
    stray.mkdir(parents=True)
    (stray / "router.py").write_text("# nothing registers this\n", encoding="utf-8")
    out, reason = auto_stage.redirect(clone, "services/active/half_built/logic.py")
    assert out == "services/staged/half_built/logic.py"
    assert reason


# ---------------------------------------------------------------------------
# FU-447: a service.toml never ships without the module its import_path names.
#
# The emission defect: the publisher writes one file, so a
# scaffold_<name>_service_toml directive lands a service.toml whose
# `import_path = services.active.<name>.router` names a router.py the PR does
# not ship. test_every_active_service_declares_a_resolvable_import_path then
# goes RED over the whole services/active tree and the auto PR can never merge.
# manifest_companions() co-ships the declared module so the import_path always
# resolves inside the SAME change.
# ---------------------------------------------------------------------------

_MANIFEST = (
    '[service]\n'
    'name = "%s"\n'
    'import_path = "services.active.%s.router"\n'
    'prefix = "/api"\ntag = "%s"\norigin = "service"\n'
    'auth = "public"\nneeds_data_layer = true\n'
)


def _module_rel(companions):
    """The single .py (non-__init__) path manifest_companions co-shipped."""
    mods = [rel for rel, _ in companions
            if rel.endswith(".py") and not rel.endswith("/__init__.py")]
    assert len(mods) == 1, companions
    return mods[0]


def _resolves(import_path, shipped):
    """The FU-447 verifier's own rule: does a candidate for import_path appear
    among the files this change ships? Kept in lock-step with
    tools/image_ship_check.resolve_module_path's dotted-path mapping."""
    p = import_path.replace(".", "/")
    cands = {p + ".py", p + "/__init__.py",
             "services/active/%s.py" % import_path,
             "services/active/%s/__init__.py" % import_path}
    return bool(cands & set(shipped))


def test_active_manifest_alone_co_ships_its_import_path_module(clone):
    """THE FU-447 CASE. An active service.toml published alone must drag its
    declared router.py into the same change, at exactly the path the verifier
    and resolve_module_path derive from import_path."""
    rel = "services/active/risk_axis_time_series/service.toml"
    content = _MANIFEST % (("risk_axis_time_series",) * 3)
    companions = auto_stage.manifest_companions(clone, rel, content)

    module_rel = _module_rel(companions)
    assert module_rel == "services/active/risk_axis_time_series/router.py"

    shipped = [rel] + [c for c, _ in companions]
    assert _resolves("services.active.risk_axis_time_series.router", shipped), (
        "manifest_companions did not make the import_path resolvable in-PR")


def test_staged_manifest_alone_co_ships_router_into_staged_not_active(clone):
    """The scaffold_<name>_service_toml shape. The module lands in the service's
    OWN staged directory -- never under services/active/, which would defeat the
    staged->active redirect boundary."""
    rel = "services/staged/cve_feed_ingestion/service.toml"
    content = _MANIFEST % (("cve_feed_ingestion",) * 3)
    companions = auto_stage.manifest_companions(clone, rel, content)

    module_rel = _module_rel(companions)
    assert module_rel == "services/staged/cve_feed_ingestion/router.py"
    assert not any(c.startswith("services/active/") for c, _ in companions)


def test_co_shipped_router_is_importable_and_exposes_router(clone):
    """The stand-in must resolve AND load: a self-contained APIRouter, no
    dangling `from .logic import`, so the manifest's promise cannot outlive the
    module even for one PR."""
    rel = "services/staged/demo_svc/service.toml"
    _, router_text = auto_stage.manifest_companions(
        clone, rel, _MANIFEST % (("demo_svc",) * 3))[0]
    assert "from .logic import" not in router_text
    ns: dict = {}
    import types, sys
    stub = types.ModuleType("fastapi")
    stub.APIRouter = lambda *a, **k: object()
    sys.modules.setdefault("fastapi", stub)
    exec(compile(router_text, rel, "exec"), ns)
    assert "router" in ns


def test_co_shipped_router_is_not_a_hollow_service_member(clone):
    """It must survive the hollow gate (zo_sentinel/gates/hollow.py): a nested
    router with zero top-level statements is hollow and would block the PR."""
    from zo_sentinel.gates import hollow
    rel = "services/staged/demo_svc/router.py"
    _, router_text = auto_stage.manifest_companions(
        clone, "services/staged/demo_svc/service.toml",
        _MANIFEST % (("demo_svc",) * 3))[0]
    assert hollow.hollow_service_member_scan(rel, router_text) is None


def test_existing_module_is_not_overwritten(clone):
    """If router.py already exists (requires-ordering landed it, or it is live
    on main) the import_path already resolves and nothing is co-shipped --
    overwriting the real surface with a stand-in would be strictly worse."""
    d = clone / "services" / "staged" / "already_has_router"
    d.mkdir(parents=True)
    (d / "router.py").write_text("router = object()\n", encoding="utf-8")
    companions = auto_stage.manifest_companions(
        clone, "services/staged/already_has_router/service.toml",
        _MANIFEST % (("already_has_router",) * 3))
    assert not any(c.endswith("/router.py") for c, _ in companions)


def test_non_manifest_and_manifestless_paths_are_untouched(clone):
    """Only a service.toml carrying an import_path triggers co-shipping."""
    assert auto_stage.manifest_companions(
        clone, "services/staged/x/router.py", "router = ...") == []
    assert auto_stage.manifest_companions(
        clone, "services/staged/x/service.toml", "name = 'x'\n") == []
    assert auto_stage.manifest_companions(
        clone, "README.md", "whatever") == []
