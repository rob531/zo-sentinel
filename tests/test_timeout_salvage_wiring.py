"""FU-166 -- the TIMEOUT branch must SALVAGE, and the wiring is the thing that broke.

Why this file exists, stated plainly, because the scar is the point:

PR #2293 shipped a CORRECT rendered-tool-call parser (`arch_salvage._render_blocks`)
and a CORRECT-looking timeout branch that called `_salvage_transcript(...)` -- while
DELETING the `_salvage_transcript` function that #2292 had added and that was live and
armed on the runtime. Every existing test stayed GREEN, because every existing test
exercised the parser in isolation and nothing asserted the parser was REACHABLE from
the path that was supposed to call it. `ruff` caught it as F821 and was the only thing
that did.

That is the FU-123 scar exactly (three correct parsers behind an exception that
guaranteed none could ever deliver a call) and the FU-116 scar exactly (guards
committed as FILES and never wired as GATES). A parser nothing calls is a file.

So these are wiring assertions, not parser assertions:

  * test_salvage_transcript_is_defined            -- RED on #2293 as filed (function deleted)
  * test_timeout_branch_calls_salvage_transcript  -- RED on main before #2293 (never called)
  * test_zero_proposed_branch_still_calls_salvage -- RED on #2293 as filed (call removed);
                                                     this is the REGRESSION control, the
                                                     half a "fix the timeout path" PR is
                                                     most likely to quietly take away
  * test_rendered_timeout_transcript_is_recovered -- the behavioural end-to-end: a real
                                                     killed-mid-flight transcript in,
                                                     directive files out

The AST checks resolve the calls structurally inside the correct handler, so a mere
mention in a comment or a docstring cannot satisfy them.
"""
import ast
import inspect
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zo_sentinel import sentinel_directive_generator_goose as gen


LONG_DESC = (
    "GET /api/verdict/audit-trail on prefix /api. logic.py reads mcp_verdicts joined "
    "to mcp_server_registry and returns the ordered verdict history per server; "
    "router.py returns {server_id, trail: [{ts, verdict, actor}]} via pydantic; "
    "Postgres-portable SQL, no network. ACCEPTANCE: contract seeds 3 verdict rows in "
    "in-memory SQLite, asserts 200, asserts trail length 3 and ordering, prints PASS."
)

# VERBATIM shape of goose's own render of an in-flight tool call, which is what
# stdout carries when the 240s wall clock kills it mid-propose. Not a
# reconstruction of what we wish it looked like.
RENDERED_TIMEOUT_TRANSCRIPT = (
    "I'll propose a service for the verdict audit trail.\n\n"
    "▸ propose_directive zo_directive_bridge\n"
    "  task: build_service_verdict_audit_trail\n"
    "  handler: build_service\n"
    "  description: " + LONG_DESC + "\n"
    "  complexity: medium\n"
    "────────────\n"
)


def _cycle_tree():
    """The AST of run_goose_cycle, parsed from the source that actually ships."""
    src = inspect.getsource(gen.run_goose_cycle)
    return ast.parse(src.lstrip())


def _calls_named(node, name):
    return any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == name
        for n in ast.walk(node)
    )


def test_salvage_transcript_is_defined():
    """RED on #2293 as filed: the PR deleted the function it called (ruff F821)."""
    assert callable(getattr(gen, "_salvage_transcript", None)), (
        "_salvage_transcript is not defined -- the timeout branch would raise "
        "NameError inside its own except handler"
    )


def test_timeout_branch_calls_salvage_transcript():
    """RED on main before #2293: the timeout handler discarded stdout wholesale."""
    handlers = [
        h
        for n in ast.walk(_cycle_tree())
        if isinstance(n, ast.Try)
        for h in n.handlers
        if h.type is not None and "TimeoutExpired" in ast.dump(h.type)
    ]
    assert handlers, "no `except subprocess.TimeoutExpired` handler found"
    assert any(_calls_named(h, "_salvage_transcript") for h in handlers), (
        "the TimeoutExpired handler does not call _salvage_transcript -- a "
        "transcript that REACHED propose_directive is still being discarded"
    )


def test_zero_proposed_branch_still_calls_salvage():
    """REGRESSION control for #2292. RED on #2293 as filed: the call was removed.

    A PR that fixes the timeout path must not silently un-fix the fenced path.
    """
    tree = _cycle_tree()
    handlers = [
        h
        for n in ast.walk(tree)
        if isinstance(n, ast.Try)
        for h in n.handlers
    ]
    in_handler = any(_calls_named(h, "_salvage_transcript") for h in handlers)
    total = _calls_named(tree, "_salvage_transcript")
    assert total, "_salvage_transcript is never called from run_goose_cycle"
    # Two distinct call sites: the delta<=0 path and the timeout handler.
    call_sites = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_salvage_transcript"
    ]
    assert len(call_sites) >= 2, (
        "expected _salvage_transcript on BOTH total-loss paths (delta<=0 and "
        "timeout); found %d call site(s)" % len(call_sites)
    )
    assert in_handler, "the timeout call site is missing"


def test_rendered_timeout_transcript_is_recovered(tmp_path, monkeypatch):
    """End-to-end: killed-mid-flight render in, a real directive file out."""
    monkeypatch.setattr(gen, "PROPOSED_DIR", tmp_path, raising=False)
    monkeypatch.setattr(gen, "_queued_stems", lambda: set(), raising=False)
    monkeypatch.setattr(gen, "_existing_anywhere", lambda: set(), raising=False)

    n = gen._salvage_transcript(RENDERED_TIMEOUT_TRANSCRIPT)

    assert n == 1, "expected exactly 1 directive recovered, got %r" % n
    written = list(tmp_path.glob("salvage_*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert payload["task"] == "build_service_verdict_audit_trail"
    assert payload["handler"] == "build_service"
    assert "ACCEPTANCE" in payload["description"]


def test_salvage_never_raises_on_garbage(monkeypatch, tmp_path):
    """A salvage failure must not take down the generator loop."""
    monkeypatch.setattr(gen, "PROPOSED_DIR", tmp_path, raising=False)
    monkeypatch.setattr(gen, "_queued_stems", lambda: set(), raising=False)
    monkeypatch.setattr(gen, "_existing_anywhere", lambda: set(), raising=False)

    assert gen._salvage_transcript(None) == 0
    assert gen._salvage_transcript("") == 0
    assert gen._salvage_transcript("  not a transcript at all") == 0
    assert list(tmp_path.glob("salvage_*.json")) == []


def test_thin_rendered_call_is_refused(monkeypatch, tmp_path):
    """Reaching the tool call does NOT lower the content bar (MIN_DESC)."""
    monkeypatch.setattr(gen, "PROPOSED_DIR", tmp_path, raising=False)
    monkeypatch.setattr(gen, "_queued_stems", lambda: set(), raising=False)
    monkeypatch.setattr(gen, "_existing_anywhere", lambda: set(), raising=False)

    thin = (
        "▸ propose_directive zo_directive_bridge\n"
        "  task: build_service_thin\n"
        "  handler: build_service\n"
        "  description: do the thing\n"
    )
    assert gen._salvage_transcript(thin) == 0
    assert list(tmp_path.glob("salvage_*.json")) == []
