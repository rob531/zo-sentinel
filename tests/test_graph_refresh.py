"""graph_refresh.needs_refresh -- pure staleness logic (no I/O; runs in CI)."""
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location(
    "gr", pathlib.Path(__file__).resolve().parents[1] / "tools" / "graph_refresh.py")
GR = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(GR)

def test_same_commit_no_refresh():
    assert GR.needs_refresh("abc123456789", "abc123456789ff") is False   # first 12 match
def test_diff_commit_refreshes():
    assert GR.needs_refresh("abc123456789", "999999999999") is True
def test_force_always():
    assert GR.needs_refresh("abc123456789", "abc123456789", force=True) is True
def test_missing_graph_refreshes():
    assert GR.needs_refresh("abc123456789", "") is True
def test_unknown_head_does_not_churn():
    assert GR.needs_refresh("", "anything") is False
