"""Durable funnel fix: promoter's default directives root must be the SAME absolute
tower path goose_runner/MCP use, so promoted directives never land where goose can't
see them. Repo-local only as a CI/test fallback."""
import importlib.util, pathlib
spec = importlib.util.spec_from_file_location(
    "promoter",
    pathlib.Path(__file__).resolve().parents[1] / "zo_sentinel" / "promoters" / "proposed_to_pending_promoter.py")
P = importlib.util.module_from_spec(spec); spec.loader.exec_module(P)


def test_prefers_tower_path_when_present(tmp_path, monkeypatch):
    tower = tmp_path / "tower" / "directives"; tower.mkdir(parents=True)
    repo = tmp_path / "repo" / "directives"; repo.mkdir(parents=True)
    monkeypatch.setattr(P, "_TOWER_DIRECTIVES", tower)
    monkeypatch.setattr(P, "_REPO_DIRECTIVES", repo)
    assert P._default_directives_root() == tower          # canonical, not repo-local


def test_repo_fallback_only_when_no_tower(tmp_path, monkeypatch):
    tower = tmp_path / "absent" / "directives"            # not created
    repo = tmp_path / "repo" / "directives"; repo.mkdir(parents=True)
    monkeypatch.setattr(P, "_TOWER_DIRECTIVES", tower)
    monkeypatch.setattr(P, "_REPO_DIRECTIVES", repo)
    assert P._default_directives_root() == repo
