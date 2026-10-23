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
