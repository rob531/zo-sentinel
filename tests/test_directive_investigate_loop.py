"""Kill the investigate_X_v2..v11 loop: collapse version suffixes so a version-bumped
re-proposal of already-done work is caught, and cap investigate_/diagnose_ per cycle.
Skip-guards if the module's import side-effects (tower-path mkdir) aren't available."""
import importlib.util, pathlib, pytest

_spec = importlib.util.spec_from_file_location(
    "dmcp", pathlib.Path(__file__).resolve().parents[1] / "zo_sentinel" / "mcp_servers" / "directive_mcp.py")
M = importlib.util.module_from_spec(_spec)
try:
    _spec.loader.exec_module(M)
except (Exception, SystemExit):
    pytest.skip("directive_mcp import side-effects unavailable", allow_module_level=True)


def test_base_task_collapses_versions():
    assert M._base_task("investigate_write_service_staleness_v4") == "investigate_write_service_staleness"
    assert M._base_task("diagnose_gap_v2_v3") == "diagnose_gap"
    assert M._base_task("build_rung_gauge_cli") == "build_rung_gauge_cli"


def test_version_bumped_reproposal_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DIRECTIVE_DIR", tmp_path)
    (tmp_path / "done").mkdir()
    (tmp_path / "investigate_x_v1.done.json").write_text("{}")
    assert M._already_done("investigate_x_v5", "investigate_x_v5") is True
    assert M._already_done("investigate_novel", "investigate_novel") is False


def test_diag_prefixes_and_cap_configured():
    assert M._DIAG_PREFIXES == ("investigate_", "diagnose_")
    assert isinstance(M._DIAG_CAP, int) and M._DIAG_CAP >= 1
