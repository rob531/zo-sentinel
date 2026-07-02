"""Tests for zo_sentinel.engine_build -- the grounded deterministic builder
engine with the bounded repair loop.

No network: the shim is an injected fake `post`. Mirrors real variance:
fenced/unfenced responses, broken-then-repaired code, edit-class directives,
shim failures, gate flipping.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from zo_sentinel import engine_build  # noqa: E402


class FakeResp:
    def __init__(self, content, status=200):
        self.status_code = status
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def make_post(*responses):
    """A post() that returns the queued responses in order (repeats the last)."""
    queue = list(responses)
    calls = []

    def post(url, **kw):
        calls.append(kw)
        r = queue.pop(0) if len(queue) > 1 else queue[0]
        return r
    post.calls = calls
    return post


def _directive(task="build_widget_api", output_file="widget_api.py"):
    return {"task": task, "output_file": output_file,
            "description": "d" * 60, "handler": "generate_file"}


PASSING = "def f():\n    return 1\n\nif __name__ == '__main__':\n    assert f() == 1\n    print('PASS')\n"
BROKEN = "def f(:\n    syntax error here\n"


def test_writes_declared_output_and_passes(tmp_path):
    post = make_post(FakeResp(f"```python\n{PASSING}```"))
    res = engine_build.build_with_engine(_directive(), "make widget_api",
                                         home=str(tmp_path), post=post,
                                         log=lambda *a: None)
    assert res["success"] is True
    assert res["repairs"] == 0
    written = (tmp_path / "widget_api.py").read_text(encoding="utf-8")
    assert "PASS" in written and "```" not in written


def test_repair_loop_fixes_broken_first_shot(tmp_path):
    post = make_post(FakeResp(BROKEN), FakeResp(PASSING))
    res = engine_build.build_with_engine(_directive(), "make widget_api",
                                         home=str(tmp_path), post=post,
                                         log=lambda *a: None)
    assert res["success"] is True
    assert res["repairs"] == 1
    assert len(post.calls) == 2
    # The repair prompt carried the ACTUAL failure detail back to the rung.
    repair_msg = post.calls[1]["json"]["messages"][1]["content"]
    assert "FAILED verification" in repair_msg and "py_compile" in repair_msg


def test_repair_is_bounded(tmp_path):
    post = make_post(FakeResp(BROKEN), FakeResp(BROKEN), FakeResp(BROKEN))
    res = engine_build.build_with_engine(_directive(), "t",
                                         home=str(tmp_path), post=post,
                                         log=lambda *a: None)
    assert res["success"] is False
    assert res["repairs"] == engine_build.MAX_REPAIRS
    assert len(post.calls) == 1 + engine_build.MAX_REPAIRS


def test_edit_class_never_writes_a_file(tmp_path):
    d = {"task": "wire_widget_into_app", "output_file": "wire_widget_into_app.py",
         "description": "d" * 60, "handler": "generate_file"}
    post = make_post(FakeResp("edited the files as asked"))
    res = engine_build.build_with_engine(d, "wire it", home=str(tmp_path),
                                         post=post, log=lambda *a: None)
    assert res["success"] is True          # parity with legacy: trust process
    assert not list(tmp_path.glob("*.py"))  # nothing written


def test_shim_error_fails_open_without_raising(tmp_path):
    post = make_post(FakeResp("", status=502))
    res = engine_build.build_with_engine(_directive(), "t", home=str(tmp_path),
                                         post=post, log=lambda *a: None)
    assert res["success"] is False and "error" in res


def test_post_exception_fails_open(tmp_path):
    def post(url, **kw):
        raise ConnectionError("shim down")
    res = engine_build.build_with_engine(_directive(), "t", home=str(tmp_path),
                                         post=post, log=lambda *a: None)
    assert res["success"] is False and "shim down" in res["error"]


def test_idempotent_rerun_same_output(tmp_path):
    post = make_post(FakeResp(PASSING))
    for _ in range(2):
        res = engine_build.build_with_engine(_directive(), "t",
                                             home=str(tmp_path), post=post,
                                             log=lambda *a: None)
        assert res["success"] is True
    assert (tmp_path / "widget_api.py").read_text(encoding="utf-8") == PASSING.strip()
    assert not list(tmp_path.glob("*.engine.tmp"))  # no tmp litter


def test_rung_escalation_by_attempt(monkeypatch):
    monkeypatch.delenv("ZO_ENGINE_RUNGS", raising=False)
    assert engine_build.rung_for_attempt(0) == engine_build.DEFAULT_RUNGS[0]
    assert engine_build.rung_for_attempt(1) == engine_build.DEFAULT_RUNGS[1]
    assert engine_build.rung_for_attempt(99) == engine_build.DEFAULT_RUNGS[-1]
    monkeypatch.setenv("ZO_ENGINE_RUNGS", "a,b,c")
    assert engine_build.rung_for_attempt(2) == "c"


def test_gate_default_off_sentinel_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv(engine_build.ENV_FLAG, raising=False)
    assert engine_build.enabled(tmp_path) is False
    sf = tmp_path / engine_build.SENTINEL_NAME
    sf.write_text("1", encoding="utf-8")
    assert engine_build.enabled(tmp_path) is True
    sf.write_text("0", encoding="utf-8")
    assert engine_build.enabled(tmp_path) is False
    monkeypatch.setenv(engine_build.ENV_FLAG, "1")
    assert engine_build.enabled(tmp_path) is True


def test_unfenced_response_and_stray_fences():
    assert engine_build.strip_code_fences("x = 1\n```\n") == "x = 1"
    assert engine_build.strip_code_fences("```python\nx = 1\n```") == "x = 1"
    assert engine_build.strip_code_fences("x = 1") == "x = 1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
