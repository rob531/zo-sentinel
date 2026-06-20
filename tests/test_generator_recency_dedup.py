"""Generator novelty fix: recently-built modules (directives/done/*.json by mtime)
surface FIRST in the architect's avoid-list, so it stops re-proposing recent builds."""
import importlib.util, json, os, pathlib, time
spec = importlib.util.spec_from_file_location(
    "gen", pathlib.Path(__file__).resolve().parents[1] / "zo_sentinel" / "sentinel_directive_generator_goose.py")
G = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(G)
except Exception:
    import pytest; pytest.skip("generator deps unavailable", allow_module_level=True)


def _done(dirp, name, output_file, mtime):
    f = dirp / f"{name}.json"
    f.write_text(json.dumps({"directive_id": name, "output_file": output_file}))
    os.utime(f, (mtime, mtime))
    return f


def test_recent_built_newest_first(tmp_path, monkeypatch):
    d = tmp_path / "done"; d.mkdir()
    _done(d, "build_alpha", "alpha.py", 1000)        # oldest
    _done(d, "build_zulu", "subdir/zulu.py", 3000)   # newest, nested path
    _done(d, "build_mike", "mike.py", 2000)
    monkeypatch.setattr(G, "_DONE_DIR", d)
    got = G._recent_built_modules()
    assert got == ["zulu.py", "mike.py", "alpha.py"]   # newest first, basename only


def test_missing_dir_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "_DONE_DIR", tmp_path / "nope")
    assert G._recent_built_modules() == []
