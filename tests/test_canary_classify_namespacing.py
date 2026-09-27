"""Negative controls for tools/canary/classify_namespacing.py.

The branch this classifier replaces failed in the generous direction and
the strict direction at once, which is the tell that it was not reading
anything: `grep -q "$VERBATIM"` matched the recipe's OWN description, so
it fired on every run, and the verdict it fired with was `INCONCLUSIVE`
+ exit 1 -- a build failure published on a transcript that contained
proof the tool had resolved.

So these tests are weighted in two directions:

  * that PASS_DISPATCHED CATCHES real resolution evidence (the dispatch
    render and the -32602 dispatcher error), and
  * that it REFUSES the recipe preamble, and refuses a genuine rename
    even though the rename's own error line contains the verbatim name.

R4 discipline: an assertion never observed failing is not evidence. Every
positive case below is paired with a negative built from the same
transcript with the one load-bearing line changed.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "tools" / "canary" / "classify_namespacing.py"
)
_spec = importlib.util.spec_from_file_location("classify_namespacing", _MODULE_PATH)
assert _spec and _spec.loader
classify_namespacing = importlib.util.module_from_spec(_spec)
sys.modules["classify_namespacing"] = classify_namespacing
_spec.loader.exec_module(classify_namespacing)

classify = classify_namespacing.classify
PASS_DIRECT = classify_namespacing.VERDICT_PASS_DIRECT
PASS_DISPATCHED = classify_namespacing.VERDICT_PASS_DISPATCHED
INCONCLUSIVE = classify_namespacing.VERDICT_INCONCLUSIVE
FAIL = classify_namespacing.VERDICT_FAIL
EXIT_CODES = classify_namespacing.EXIT_CODES

NONCE = "ns-30532-21712"

# The recipe preamble goose prints before it does anything. Every line of
# it names the verbatim tool. Reproduced from run 35426553926.
PREAMBLE = """--- namespacing ---
Loading recipe: stdio namespacing probe (scar #454)
Description: Scar #454: goose 1.38 namespaced stdio tools away from what the architect recipe expects and starved the architect (every call -> Tool not found -> +0). This probe loads the REAL zo_directive_bridge stdio extension (same cmd/args as directive_architect.yaml, cwd must be /home/workspace/zo_sentinel) and makes ONE read-only call to the VERBATIM name the architect recipe hardcodes: zo_directive_bridge__read_protected_files. If a candidate goose version presents different tool names, the call fails and the nonce marker never appears: canary FAIL, do not flip.

Parameters used to load this recipe:
   nonce: ns-30532-21712

    __( O)>  * new session * openai openai/gpt-oss-120b
   \\____)    20260919_2 * /home/runner/work/zo-sentinel/zo-sentinel
     L L     goose is ready
"""

# Run 35426553926 (2026-09-19), the run chairman issue #4077 asks to be
# read. Every line below appeared in that log.
REAL_0919_TRANSCRIPT = PREAMBLE + """
  ------------------------------
  > read_protected_files zo_directive_bridge

Ran into this error: Request failed: Bad request (400): 'messages.2' : for 'role:assistant' the following must be satisfied[('messages.2' : property 'reasoning_content' is unsupported)].

Please retry if you think this is a transient or recoverable error.
"""


# ---------------------------------------------------------------- catches

def test_real_0919_run_is_a_pass_not_an_inconclusive():
    """The regression this file exists for.

    The bash branch published INCONCLUSIVE + exit 1 on this transcript.
    goose dispatched the tool in it.
    """
    verdict, evidence = classify(REAL_0919_TRANSCRIPT, NONCE)
    assert verdict == PASS_DISPATCHED, evidence
    assert EXIT_CODES[verdict] == 0
    assert "read_protected_files" in evidence


def test_direct_evidence_outranks_everything():
    transcript = REAL_0919_TRANSCRIPT + f"\nNAMESPACING_OK::{NONCE}\n"
    verdict, _ = classify(transcript, NONCE)
    assert verdict == PASS_DIRECT


def test_dispatcher_error_is_positive_evidence():
    transcript = PREAMBLE + (
        "\n-32602: Tool arguments for zo_directive_bridge__read_protected_files"
        " did not match the schema\n")
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == PASS_DISPATCHED, evidence


# ---------------------------------------------------------------- refuses

def test_preamble_alone_is_never_read_as_evidence():
    """THE load-bearing negative control.

    The preamble names the verbatim tool four times AND contains the
    phrase "Tool not found" inside its own description. So asserting
    only on the VERDICT is not enough: with scrubbing disabled this
    transcript still returns FAIL -- via the not-found branch, reading
    the recipe's prose as if it were the world. That is the rubber stamp
    the mutation control caught on 2026-09-19.

    The claim under test is therefore about the EVIDENCE: nothing in the
    preamble may reach any branch. Only the terminal silent-rename line
    is admissible here.
    """
    verdict, evidence = classify(PREAMBLE, NONCE)
    assert verdict == FAIL, evidence
    assert EXIT_CODES[verdict] == 1
    assert evidence.startswith("RENAME: "), evidence
    assert "appears nowhere outside the recipe preamble" in evidence, evidence
    assert "Description:" not in evidence, evidence
    assert "Scar #454" not in evidence, evidence


def test_scrub_removes_every_line_that_names_the_tool():
    """Stated directly, so the scrub has an assertion of its own rather
    than only being implied by a verdict two branches away."""
    body = classify_namespacing.scrub_recipe_preamble(PREAMBLE)
    assert "zo_directive_bridge__read_protected_files" not in body
    assert "Tool not found" not in body


def test_genuine_rename_is_a_fail_even_though_its_error_names_the_tool():
    """The 1.38 starvation shape.

    goose's not-found error CONTAINS the verbatim name. A dispatch-
    evidence check that ran before the not-found check would read this
    as a pass -- a false green on the one condition the probe exists to
    catch.
    """
    transcript = PREAMBLE + (
        "\n  > read_protected_files zo_directive_bridge\n"
        "Tool not found: zo_directive_bridge__read_protected_files\n")
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == FAIL, evidence
    assert "RENAME" in evidence


def test_extension_start_failure_is_a_fail_not_an_inconclusive():
    """goose warns and runs on without the bridge. An architect with no
    bridge is dead either way, so no later branch may excuse it -- not
    even a provider signal sitting in the same transcript."""
    transcript = PREAMBLE + (
        "\nFailed to start extension 'zo_directive_bridge', continuing without it\n"
        "Bad request (400): property 'reasoning_content' is unsupported\n"
        "  > read_protected_files zo_directive_bridge\n")
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == FAIL, evidence
    assert "EXTENSION_START_FAILURE" in evidence


def test_provider_signal_without_resolution_evidence_is_inconclusive():
    """Same 400 as the real run, but goose never reached a dispatch.

    This is the pole that proves the PASS in
    test_real_0919_run_is_a_pass_not_an_inconclusive is carried by the
    dispatch line and not by the 400 being tolerated.
    """
    transcript = PREAMBLE + (
        "\nRan into this error: Request failed: Bad request (400): 'messages.2' :"
        " for 'role:assistant' the following must be satisfied"
        "[('messages.2' : property 'reasoning_content' is unsupported)].\n")
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == INCONCLUSIVE, evidence
    assert EXIT_CODES[verdict] == 2


def test_billing_refusal_is_inconclusive_not_a_rename():
    transcript = PREAMBLE + (
        "\nwarning: Please check your account with your provider to add more"
        " credits, then resend your message to continue.\n")
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == INCONCLUSIVE, evidence


def test_silent_transcript_is_a_fail():
    """Nothing resolved, nothing refused. That IS the version verdict."""
    verdict, evidence = classify(PREAMBLE + "\nsession ended\n", NONCE)
    assert verdict == FAIL, evidence


def test_empty_nonce_is_rejected():
    with pytest.raises(ValueError):
        classify(REAL_0919_TRANSCRIPT, "")
