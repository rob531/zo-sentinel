"""Negative controls for tools/canary/classify_smoke.py.

The bug this file guards against is not "the classifier is wrong". It is
"the classifier is generous" -- a PROVIDER_UNAVAILABLE bucket wide enough
to swallow a real goose regression would turn the goose-canary from a
gate that publishes false reds into a gate that publishes false greens,
which is strictly worse. So the tests below are weighted: three assert
the new bucket CATCHES what it must, and six assert it REFUSES what it
must not.

R4 discipline: an assertion never observed failing is not evidence. Each
positive case here has a paired negative built from the same transcript
with the provider signal removed.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "tools" / "canary" / "classify_smoke.py"
)
_spec = importlib.util.spec_from_file_location("classify_smoke", _MODULE_PATH)
assert _spec and _spec.loader
classify_smoke = importlib.util.module_from_spec(_spec)
sys.modules["classify_smoke"] = classify_smoke
_spec.loader.exec_module(classify_smoke)

classify = classify_smoke.classify
PASS = classify_smoke.VERDICT_PASS
UNAVAILABLE = classify_smoke.VERDICT_PROVIDER_UNAVAILABLE
FAIL = classify_smoke.VERDICT_FAIL

NONCE = "ci-25773-28923"

# Reproduced from run 33387672279 (2026-08-31), the run chairman issue
# #4077 asks to be read and repaired. Trimmed to the lines that matter;
# every line below appeared verbatim in that log.
REAL_0831_TRANSCRIPT = """--- smoke ---
Loading recipe: goose canary smoke
Description: Minimal recipe to prove a CANDIDATE goose version still (a) loads a recipe, (b) drives a model, and (c) executes a developer/shell tool call.

Parameters used to load this recipe:
   nonce: ci-25773-28923

    __( O)>  * new session * openai gpt-oss-120b
   \\____)    20260831_1 * /home/runner/work/zo-sentinel/zo-sentinel
     L L     goose is ready

warning: Please check your account with your provider to add more credits, then resend your message to continue.
"""

# The same run with the billing line deleted: goose booted, drove nothing,
# echoed nothing, and gave no reason. That IS a version verdict.
REAL_0831_WITHOUT_BILLING_LINE = REAL_0831_TRANSCRIPT.replace(
    "warning: Please check your account with your provider to add more credits,"
    " then resend your message to continue.\n",
    "",
)

HEALTHY_TRANSCRIPT = f"""--- smoke ---
Loading recipe: goose canary smoke
    __( O)>  * new session * openai gpt-oss-120b
     L L     goose is ready

--- developer | shell ---
command: echo "CANARY_TOOLCALL_OK::{NONCE}"

CANARY_TOOLCALL_OK::{NONCE}

I ran the command and it printed the token. Stopping as instructed.
"""


# --------------------------------------------------------------------
# 1. The bucket catches what it exists for.
# --------------------------------------------------------------------

def test_the_real_2026_08_31_failure_is_not_a_goose_verdict():
    """The regression case. Before this module, this transcript produced
    'this goose broke our recipe/tool loop'."""
    verdict, evidence = classify(REAL_0831_TRANSCRIPT, NONCE)
    assert verdict == UNAVAILABLE
    assert "billing" in evidence
    assert "add more credits" in evidence


@pytest.mark.parametrize(
    "line,expect_label",
    [
        ("Error: 429 Too Many Requests", "ratelimit"),
        ("provider returned rate_limit_exceeded", "ratelimit"),
        ("status code: 401 returned by the provider", "auth"),
        ("Incorrect API key provided", "auth"),
        ("insufficient_quota: your account is out of credit", "billing"),
        ("503 Service Unavailable", "upstream"),
        ("error sending request for url (https://api.cerebras.ai/v1/chat/completions)", "transport"),
        ("connection refused", "transport"),
    ],
)
def test_each_provider_signal_class_is_recognised(line, expect_label):
    transcript = HEALTHY_TRANSCRIPT.replace(
        f"CANARY_TOOLCALL_OK::{NONCE}\n\n", ""
    ).replace(f'echo "CANARY_TOOLCALL_OK::{NONCE}"', "echo (redacted)") + "\n" + line + "\n"
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == UNAVAILABLE, evidence
    assert evidence.startswith(expect_label)


# --------------------------------------------------------------------
# 2. NEGATIVE CONTROLS -- the bucket refuses what it must not absorb.
#    If any of these ever returns PROVIDER_UNAVAILABLE, a real goose
#    regression is being excused as an outage.
# --------------------------------------------------------------------

def test_same_run_without_the_billing_line_is_a_real_fail():
    """The paired negative for the headline case. Identical transcript,
    provider signal removed -> the verdict must flip to FAIL."""
    verdict, _ = classify(REAL_0831_WITHOUT_BILLING_LINE, NONCE)
    assert verdict == FAIL


def test_silent_loop_failure_is_fail_not_unavailable():
    """goose ran, called the tool, and the nonce never came back. This is
    precisely the 1.38-shaped regression the canary exists to catch."""
    transcript = HEALTHY_TRANSCRIPT.replace(f"CANARY_TOOLCALL_OK::{NONCE}", "(no output)")
    verdict, _ = classify(transcript, NONCE)
    assert verdict == FAIL


def test_tool_not_found_is_fail_not_unavailable():
    transcript = HEALTHY_TRANSCRIPT.replace(
        f"CANARY_TOOLCALL_OK::{NONCE}", "Tool not found: developer__shell"
    )
    verdict, _ = classify(transcript, NONCE)
    assert verdict == FAIL


def test_model_prose_about_credits_does_not_excuse_a_failure():
    """The talked-into-it case. The model discussing credits, quotas or
    rate limits in its own answer must not buy the run an INCONCLUSIVE."""
    transcript = HEALTHY_TRANSCRIPT.replace(
        f"CANARY_TOOLCALL_OK::{NONCE}",
        "I considered whether you have enough credits or a rate limit issue, "
        "but I will not run the command.",
    )
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == FAIL, evidence


def test_bare_numbers_do_not_match():
    """429 and 401 as ordinary integers -- e.g. inside a nonce or a token
    count -- must not be read as HTTP statuses."""
    transcript = HEALTHY_TRANSCRIPT.replace(
        f"CANARY_TOOLCALL_OK::{NONCE}", "total tokens: 429 prompt, 401 completion"
    )
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == FAIL, evidence


def test_bridge_start_failure_stays_out_of_this_bucket():
    """`Failed to start extension` is OUR extension, not the provider.
    The namespacing step owns that diagnosis and must keep owning it."""
    transcript = HEALTHY_TRANSCRIPT.replace(
        f"CANARY_TOOLCALL_OK::{NONCE}",
        "Failed to start extension 'zo_directive_bridge', continuing without it",
    )
    verdict, _ = classify(transcript, NONCE)
    assert verdict == FAIL


# --------------------------------------------------------------------
# 3. Ordering: direct evidence of success outranks a survived symptom.
# --------------------------------------------------------------------

def test_nonce_wins_over_a_transient_rate_limit():
    """A 429 on turn one that goose retried past, then a green loop. If
    the provider check ran first this would downgrade to INCONCLUSIVE and
    the canary would never go green on a busy afternoon."""
    transcript = (
        "warning: 429 Too Many Requests, retrying\n" + HEALTHY_TRANSCRIPT
    )
    verdict, evidence = classify(transcript, NONCE)
    assert verdict == PASS
    assert NONCE in evidence


def test_pass_requires_the_exact_nonce():
    """A stale nonce from a previous run must not satisfy this run."""
    transcript = HEALTHY_TRANSCRIPT.replace(NONCE, "ci-00000-00000")
    verdict, _ = classify(transcript, NONCE)
    assert verdict == FAIL


# --------------------------------------------------------------------
# 4. Exit-code contract -- the workflow branches on these three integers.
# --------------------------------------------------------------------

def test_exit_codes_are_three_distinct_values():
    codes = classify_smoke.EXIT_CODES
    assert codes[PASS] == 0
    assert codes[FAIL] == 1
    assert codes[UNAVAILABLE] == 2
    assert len(set(codes.values())) == 3


def test_cli_round_trip(tmp_path):
    path = tmp_path / "smoke.out"
    path.write_text(REAL_0831_TRANSCRIPT, encoding="utf-8")
    rc = classify_smoke.main(["--transcript", str(path), "--nonce", NONCE])
    assert rc == 2

    path.write_text(HEALTHY_TRANSCRIPT, encoding="utf-8")
    assert classify_smoke.main(["--transcript", str(path), "--nonce", NONCE]) == 0

    path.write_text(REAL_0831_WITHOUT_BILLING_LINE, encoding="utf-8")
    assert classify_smoke.main(["--transcript", str(path), "--nonce", NONCE]) == 1


def test_missing_transcript_is_unknown_not_fail(tmp_path):
    """R6: unknown != zero. No transcript means goose never got far enough
    to write one, which is not a version verdict either."""
    rc = classify_smoke.main(
        ["--transcript", str(tmp_path / "nope.out"), "--nonce", NONCE]
    )
    assert rc == 2


def test_empty_nonce_is_rejected():
    with pytest.raises(ValueError):
        classify("anything", "")
