"""Negative controls for the architect fenced-directive salvage.

HARNESS DOCTRINE R4: an assertion never seen RED is not evidence. Every refusal
below has been observed refusing; the positive case was observed recovering 3
real build_service directives from the transcript the harness discarded at
2026-07-29T11:56:26Z.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from zo_sentinel.arch_salvage import extract_directives, salvage

LONG = "GET /api/x on prefix /api. logic.py reads mcp_server_registry and returns counts via pydantic. ACCEPTANCE: contract seeds 3 rows, asserts 200, prints PASS."


def _fence(body):
    return "```json\n" + body + "\n```"


def test_no_json_recovers_nothing():
    assert extract_directives("I looked at the repo and found nothing to do.") == []


def test_json_that_is_not_a_directive_is_refused():
    assert extract_directives(_fence('{"foo": 1, "bar": "baz"}')) == []


def test_thin_description_is_refused():
    # A thin description GHOSTS at the builder seam.
    blob = _fence('{"task":"build_x","handler":"build_service","description":"do it"}')
    assert extract_directives(blob) == []


def test_unknown_handler_is_refused():
    blob = _fence('{"task":"build_x","handler":"edit_file","description":"%s"}' % LONG)
    assert extract_directives(blob) == []


def test_generate_file_without_output_file_is_refused():
    # output_file:null no-ops at the builder seam -- never queue one.
    blob = _fence('{"task":"build_x","handler":"generate_file","description":"%s"}' % LONG)
    assert extract_directives(blob) == []


def test_well_formed_build_service_is_recovered():
    blob = _fence('{"task":"build_service_ok","handler":"build_service","description":"%s","complexity":"low"}' % LONG)
    got = extract_directives(blob)
    assert len(got) == 1 and got[0]["task"] == "build_service_ok"


def test_already_queued_task_is_not_rewritten():
    blob = _fence('{"task":"build_service_ok","handler":"build_service","description":"%s"}' % LONG)
    wrote = []
    n = salvage(blob, queued_stems={"build_service_ok"}, existing_files=set(),
                stamp="T", writer=lambda f, p: wrote.append(f))
    assert n == 0 and wrote == []


def test_novel_task_is_written_with_provenance():
    blob = _fence('{"task":"build_service_novel","handler":"build_service","description":"%s"}' % LONG)
    wrote = {}
    n = salvage(blob, queued_stems=set(), existing_files=set(),
                stamp="T", writer=lambda f, p: wrote.setdefault(f, p))
    assert n == 1
    (name, payload), = wrote.items()
    assert name == "salvage_T_build_service_novel.json"
    assert "SALVAGED" in payload["rationale"]
    assert payload["handler"] == "build_service"


def test_existing_output_file_is_not_rebuilt():
    blob = _fence('{"task":"build_dup","handler":"generate_file","output_file":"already.py","description":"%s"}' % LONG)
    n = salvage(blob, queued_stems=set(), existing_files={"already.py"},
                stamp="T", writer=lambda f, p: None)
    assert n == 0


def test_cap_is_enforced_per_cycle():
    many = "\n".join(
        _fence('{"task":"t%d","handler":"build_service","description":"%s"}' % (i, LONG))
        for i in range(20)
    )
    assert salvage(many, set(), set(), "T", lambda f, p: None) == 5




# --- Shape 2: RENDERED tool calls recovered from a TIMEOUT transcript --------
# Fixture is the real 2026-07-29T12:20:53Z timeout transcript shape (goose
# renders the tool call it was making when the wall clock killed it).

REAL_TIMEOUT_RENDER = """
  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  \u25b8 propose_directive zo_directive_bridge
    task: build_service_circuit_breaker_status
    handler: build_service
    description: GET /api/circuit-breaker/status on prefix /api. logic.py reads service_health for the gate_orchestrator row, also queries mcp_server_registry for aggregate counts. router.py returns breaker_state via pydantic. Postgres-portable SQL, no network. ACCEPTANCE: contract seeds a fake gate_orchestrator row plus 2 healthy and 1 stale daemon in in-memory SQLite, asserts 200, prints PASS.

  \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
  \u25b8 propose_directive zo_directive_bridge
    task: build_service_verdict_audit_trail
    handler: build_service
    description: GET /api/servers/{server_id}/audit-trail on prefix /api. FastAPI router plus pydantic response model. logic.py queries audit_log JOIN mcp_server_registry ORDER BY timestamp DESC LIMIT 50. Contract seeds 2 audit rows, asserts 200, asserts entries length, prints PASS.
"""


def test_timeout_render_recovers_both_calls():
    got = extract_directives(REAL_TIMEOUT_RENDER)
    tasks = sorted(d["task"] for d in got)
    assert tasks == ["build_service_circuit_breaker_status",
                     "build_service_verdict_audit_trail"], tasks


def test_render_without_propose_directive_recovers_nothing():
    # NEGATIVE CONTROL: the same key/value shape, but no tool call was reached.
    txt = "    task: build_service_x\n    handler: build_service\n    description: %s\n" % LONG
    assert extract_directives(txt) == []


def test_render_with_thin_description_is_refused():
    # NEGATIVE CONTROL: reaching the tool call does not lower the content bar.
    txt = "  \u25b8 propose_directive zo_directive_bridge\n    task: build_x\n    handler: build_service\n    description: do it\n"
    assert extract_directives(txt) == []


def test_render_with_unknown_handler_is_refused():
    txt = ("  \u25b8 propose_directive zo_directive_bridge\n    task: build_x\n"
           "    handler: edit_file\n    description: %s\n" % LONG)
    assert extract_directives(txt) == []


def test_render_and_fenced_do_not_double_count_one_task():
    fenced = _fence('{"task":"build_service_dup","handler":"build_service","description":"%s"}' % LONG)
    rendered = ("  \u25b8 propose_directive zo_directive_bridge\n    task: build_service_dup\n"
                "    handler: build_service\n    description: %s\n" % LONG)
    assert len(extract_directives(fenced + "\n" + rendered)) == 1

if __name__ == "__main__":
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError:
                print("FAIL", name)
                fails.append(name)
    print("RESULT:", "ALL PASS" if not fails else "FAILURES: %s" % fails)
    sys.exit(1 if fails else 0)

