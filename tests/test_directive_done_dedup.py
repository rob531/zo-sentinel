"""Bridge-level authoritative done-dedup: propose_directive rejects re-proposals of
tasks whose .done.json sentinel exists (reconciles architect graph-view vs the
pipeline's done-record -> stops the proposed/ clog that drives the +0 stall).
Module does tower-path mkdir at import, so this skip-guards in CI and runs on tower."""
import importlib.util, pathlib, pytest
spec = importlib.util.spec_from_file_location(
    "dmcp", pathlib.Path(__file__).resolve().parents[1] / "zo_sentinel" / "mcp_servers" / "directive_mcp.py")
M = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(M)
except (Exception, SystemExit):   # SystemExit is BaseException: FU-158
    pytest.skip("directive_mcp import side-effects unavailable in CI", allow_module_level=True)


def test_already_done_rejects_built_passes_novel(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DIRECTIVE_DIR", tmp_path)
    (tmp_path / "done").mkdir()
    (tmp_path / "build_snow_connector.done.json").write_text("{}")
    (tmp_path / "done" / "build_aidr_commit_gateway.json").write_text("{}")
    assert M._already_done("build_snow_connector", "build_snow_connector") is True
    assert M._already_done("build_aidr_commit_gateway", "build_aidr_commit_gateway") is True
    assert M._already_done("build_rung_gauge_cli", "build_rung_gauge_cli") is False
