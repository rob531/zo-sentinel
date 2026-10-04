"""The quarantine manifest's own managers must not count as live referrers.

Two poles, and the RED one is the point: without the marker the manager's
mention IS counted (observed), with it the mention is ignored. An exclusion
never seen to change a count is not an exclusion, it is a comment (R4).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_rq_under_test", REPO / "tools" / "requeue_quarantined.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _tree(root: Path, body: str) -> None:
    (root / "widget_thing.py").write_text("def go():\n    return 1\n", encoding="utf-8")
    (root / "mentions_it.py").write_text(body, encoding="utf-8")


def test_a_plain_mention_counts_as_a_live_referrer(tmp_path):
    """RED pole: this is the behaviour that made a withdrawn module eligible."""
    rq = _load()
    _tree(tmp_path, "# see widget_thing.py for the old shape\n")
    refs = rq.reference_counts(tmp_path, ["widget_thing.py"])["widget_thing.py"]
    assert refs["kind"] != "unmeasurable"
    assert refs["refs"] == ["mentions_it.py"], refs


def test_a_self_declared_manager_does_not_count(tmp_path):
    """GREEN pole: the identical mention, in a file that declares itself."""
    rq = _load()
    _tree(tmp_path, "MARK = %r\n# see widget_thing.py for the old shape\n"
          % rq._MANAGER_MARK)
    refs = rq.reference_counts(tmp_path, ["widget_thing.py"])["widget_thing.py"]
    assert refs["refs"] == [], refs
    assert refs["kind"] != "unmeasurable", "excluded != unmeasurable (R6)"


def test_the_shipped_managers_declare_themselves(tmp_path):
    """The marker is only worth having if the managers actually carry it."""
    rq = _load()
    land = REPO / "tools" / "stranded_land.py"
    assert land.is_file(), "the lander should be in the repo by now"
    assert rq._MANAGER_MARK in land.read_text(encoding="utf-8")
