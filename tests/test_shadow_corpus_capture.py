"""
test_shadow_corpus_capture.py -- REPORT-ONLY shadow-corpus capture hooks.

The ONE thing that must never regress: capture can NEVER block, slow, or break
the builder. These tests prove the three properties the chairman is paranoid
about, for BOTH hooks (prompt/response at the ladder shim, PR<->build join in
the publisher):

  (a) flag ON  -> a call / PR-open appends the expected JSONL line.
  (b) a forced exception inside the hook (unwritable path) is SWALLOWED and the
      caller / build path proceeds normally. <-- the critical one.
  (c) flag OFF -> no file is written and behavior is byte-identical.

Everything runs hermetically: the shim's escalation.ask() is monkeypatched (no
model call, no keys) and the publisher runs on throwaway git repos.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import shadow_corpus_capture as scc  # noqa: E402


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --------------------------------------------------------------------------
# Helper-level: the fail-graceful contract in isolation
# --------------------------------------------------------------------------
def test_flag_off_is_inert(tmp_path, monkeypatch):
    monkeypatch.delenv(scc.CAPTURE_FLAG, raising=False)
    pr = tmp_path / "goose_prompt_response.jsonl"
    monkeypatch.setattr(scc, "PROMPT_RESPONSE_LOG", str(pr))
    assert scc.capture_enabled() is False
    assert scc.capture_prompt_response(model="m", prompt="p", response="r") is False
    assert not pr.exists()          # (c) no write at all


def test_flag_on_writes_expected_line(tmp_path, monkeypatch):
    monkeypatch.setenv(scc.CAPTURE_FLAG, "1")
    pr = tmp_path / "goose_prompt_response.jsonl"
    monkeypatch.setattr(scc, "PROMPT_RESPONSE_LOG", str(pr))
    ok = scc.capture_prompt_response(
        model="zo-ladder-low", prompt="build X", response="def f(): ...",
        finish_reason="stop", directive_id="d-1", task="build_x", tier="low")
    assert ok is True               # (a)
    rows = _read_jsonl(pr)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"ts", "directive_id", "task", "tier", "model", "prompt",
                        "response", "prompt_tokens", "response_tokens", "finish_reason"}
    assert row["model"] == "zo-ladder-low"
    assert row["directive_id"] == "d-1"
    assert row["response"] == "def f(): ..."
    assert row["prompt_tokens"] is None and row["response_tokens"] is None


def test_forced_exception_is_swallowed(tmp_path, monkeypatch):
    """(b) an unwritable target must NOT raise -- returns False, caller unharmed."""
    monkeypatch.setenv(scc.CAPTURE_FLAG, "1")
    # Point the log at a path whose parent is a FILE, so os.makedirs + open fail.
    wall = tmp_path / "not_a_dir"
    wall.write_text("x")
    monkeypatch.setattr(scc, "PROMPT_RESPONSE_LOG", str(wall / "sub" / "x.jsonl"))
    monkeypatch.setattr(scc, "PR_LEDGER_LOG", str(wall / "sub" / "y.jsonl"))
    monkeypatch.setattr(scc, "FAILURE_LOG", str(wall / "sub" / "z.log"))
    # No exception escapes; both hooks just report False.
    assert scc.capture_prompt_response(model="m", prompt="p", response="r") is False
    assert scc.capture_pr_publish(pr_url="https://x/pull/7", file="a.py") is False


def test_pr_number_from_url():
    assert scc.pr_number_from_url("https://github.com/o/r/pull/8712") == 8712
    assert scc.pr_number_from_url("https://x/pull/7/") == 7
    assert scc.pr_number_from_url(None) is None
    assert scc.pr_number_from_url("no-number-here") is None


# --------------------------------------------------------------------------
# Hook 1: end-to-end through the ladder shim endpoint
# --------------------------------------------------------------------------
def _shim_or_skip():
    pytest.importorskip("fastapi")
    pytest.importorskip("uvicorn")
    try:
        import ladder_shim
    except Exception as e:
        pytest.skip(f"ladder_shim not importable here: {e}", allow_module_level=False)
    return ladder_shim


class _FakeResult:
    success = True
    text = "def built(): return 42"
    tool_calls = None
    backend = "fake"
    model = "fake-model-xyz"
    error = None
    attempts = []


def _client_with_fake_ladder(ladder_shim, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(ladder_shim.escalation, "ask",
                        lambda **kw: _FakeResult())
    monkeypatch.setattr(ladder_shim.escalation, "task_for_model",
                        lambda m: "builder")
    return TestClient(ladder_shim.app)


_CHAT_REQ = {"model": "zo-ladder-low", "stream": False,
             "messages": [{"role": "user", "content": "build X"}]}


def test_hook1_flag_on_writes_line(tmp_path, monkeypatch):
    ladder_shim = _shim_or_skip()
    monkeypatch.setenv(scc.CAPTURE_FLAG, "1")
    pr = tmp_path / "goose_prompt_response.jsonl"
    monkeypatch.setattr(scc, "PROMPT_RESPONSE_LOG", str(pr))
    client = _client_with_fake_ladder(ladder_shim, monkeypatch)
    resp = client.post("/v1/chat/completions", json=_CHAT_REQ)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "def built(): return 42"
    rows = _read_jsonl(pr)
    assert len(rows) == 1
    assert rows[0]["response"] == "def built(): return 42"
    assert rows[0]["tier"] == "zo-ladder-low"      # Goose sends tier as model id
    assert rows[0]["finish_reason"] == "stop"


def test_hook1_flag_off_no_write_same_response(tmp_path, monkeypatch):
    ladder_shim = _shim_or_skip()
    monkeypatch.delenv(scc.CAPTURE_FLAG, raising=False)
    pr = tmp_path / "goose_prompt_response.jsonl"
    monkeypatch.setattr(scc, "PROMPT_RESPONSE_LOG", str(pr))
    client = _client_with_fake_ladder(ladder_shim, monkeypatch)
    resp = client.post("/v1/chat/completions", json=_CHAT_REQ)
    assert resp.status_code == 200                 # (c) unchanged response
    assert resp.json()["choices"][0]["message"]["content"] == "def built(): return 42"
    assert not pr.exists()                         # (c) no write


def test_hook1_capture_exception_does_not_break_response(tmp_path, monkeypatch):
    """(b) if capture blows up, the shim still returns the model response."""
    ladder_shim = _shim_or_skip()
    monkeypatch.setenv(scc.CAPTURE_FLAG, "1")

    def _boom(**kw):
        raise RuntimeError("capture exploded")
    monkeypatch.setattr(ladder_shim._shadow_corpus, "capture_prompt_response", _boom)
    client = _client_with_fake_ladder(ladder_shim, monkeypatch)
    resp = client.post("/v1/chat/completions", json=_CHAT_REQ)
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "def built(): return 42"


# --------------------------------------------------------------------------
# Hook 2: end-to-end through the publisher on a throwaway git repo
# --------------------------------------------------------------------------
def _git(cwd, *a):
    p = subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, text=True)
    assert p.returncode == 0, (a, p.stderr)
    return p.stdout


@pytest.fixture
def pub_world(tmp_path):
    from zo_sentinel.publisher.gitops import PublishResult
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    home = tmp_path / "runtime"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    _git(tmp_path, "init", "-q", "-b", "main", str(seed))
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "base.py").write_text("B = 1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "origin", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(home))
    (home / "directives").mkdir()

    class FakePublish:
        def __init__(self):
            self.n = 0

        def publish(self, plan):
            self.n += 1
            # mimic the real producer: write the file so refusal().read works upstream
            return PublishResult(ok=True, branch=plan.branch,
                                 pr_url="https://github.com/rob531/zo-sentinel/pull/%d" % self.n)

    return {"tmp": tmp_path, "home": home, "gitops": FakePublish()}


def _make_pc(pub_world, monkeypatch):
    from zo_sentinel.publisher.publisher import ProducerCommit
    return ProducerCommit(gitops=pub_world["gitops"], home=str(pub_world["home"]),
                          outbox=str(pub_world["tmp"] / "state" / "outbox.json"),
                          quarantine_dir=str(pub_world["tmp"] / "quarantine"),
                          enabled_override=True, pr_spacing_sec=0,
                          sleep=lambda s: None)


# a real (non-hollow) API file that passes the pre-PR refusal rules
_GOOD = ("from fastapi import APIRouter\nfrom app.db import get_session\n"
         "router = APIRouter()\n@router.get('/y')\ndef y():\n    return 1\n")


def test_hook2_flag_on_writes_ledger(pub_world, monkeypatch):
    monkeypatch.setenv(scc.CAPTURE_FLAG, "1")
    ledger = pub_world["tmp"] / "pr_publisher_ledger.jsonl"
    monkeypatch.setattr(scc, "PR_LEDGER_LOG", str(ledger))
    home = pub_world["home"]
    (home / "real_api.py").write_text(_GOOD)
    pc = _make_pc(pub_world, monkeypatch)
    res = pc.commit("real_api.py", task="build_real_api", tier="zo-ladder-low")
    assert res["action"] == "published"            # build path unaffected
    rows = _read_jsonl(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"ts", "build_id", "directive_id", "pr_number", "pr_url",
                        "branch", "file", "built_at", "task", "tier", "model"}
    assert row["directive_id"] == "build_real_api"
    assert row["file"] == "real_api.py"
    assert row["pr_number"] == 1
    assert row["tier"] == "zo-ladder-low"
    assert row["pr_url"].endswith("/pull/1")


def test_hook2_flag_off_no_write_still_publishes(pub_world, monkeypatch):
    monkeypatch.delenv(scc.CAPTURE_FLAG, raising=False)
    ledger = pub_world["tmp"] / "pr_publisher_ledger.jsonl"
    monkeypatch.setattr(scc, "PR_LEDGER_LOG", str(ledger))
    home = pub_world["home"]
    (home / "real_api.py").write_text(_GOOD)
    pc = _make_pc(pub_world, monkeypatch)
    res = pc.commit("real_api.py", task="build_real_api", tier="zo-ladder-low")
    assert res["action"] == "published"            # (c) identical outcome
    assert not ledger.exists()                     # (c) no write


def test_hook2_capture_exception_does_not_break_publish(pub_world, monkeypatch):
    """(b) if the join-ledger hook blows up, the PR still publishes cleanly."""
    monkeypatch.setenv(scc.CAPTURE_FLAG, "1")
    from zo_sentinel.publisher import publisher as pub_mod

    def _boom(**kw):
        raise RuntimeError("ledger exploded")
    monkeypatch.setattr(pub_mod._shadow_corpus, "capture_pr_publish", _boom)
    home = pub_world["home"]
    (home / "real_api.py").write_text(_GOOD)
    pc = _make_pc(pub_world, monkeypatch)
    res = pc.commit("real_api.py", task="build_real_api", tier="zo-ladder-low")
    assert res["action"] == "published"            # publish survives the blow-up
    assert res["pr_url"].endswith("/pull/1")
