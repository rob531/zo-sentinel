#!/usr/bin/env python3
"""Classify a goose-canary smoke transcript into THREE outcomes, not two.

WHY THIS EXISTS (chairman issue #4077, run 33387672279, 2026-08-31)
-------------------------------------------------------------------
The `Tool-call smoke` step of goose-canary.yml had exactly two outcomes:
the nonce appeared (PASS) or it did not, in which case the step printed

    SMOKE FAIL: nonce not echoed -- this goose broke our recipe/tool loop

and exited 1. On 2026-08-31 the transcript for that run contained:

    warning: Please check your account with your provider to add more
    credits, then resend your message to continue.

The candidate goose loaded the recipe, started a session and reached the
provider. The provider then refused on BILLING. Nothing whatsoever was
learned about the goose version -- and the canary published, in the
imperative register of a version verdict, that this goose broke our
recipe/tool loop. That verdict is false, and it is the reason the lane
read red for 30 days without anyone able to act on it.

This is the FU-108 rule (EXTRACTION_FAILURE != DEGENERATE, never let one
bucket absorb the other) and HARNESS DOCTRINE R6 (unknown != zero)
applied to the one step in this workflow that lacked them. The
`namespacing` step below it already has three outcomes and an explicit
PROBE INCONCLUSIVE branch; the shim tier already has a negative control.
The smoke step -- the first step, the one that actually failed -- had
neither.

CONTRACT
--------
    classify(transcript, nonce) -> (verdict, evidence)

    PASS                  exit 0   the nonce was echoed. DIRECT evidence
                                   the full recipe->model->tool loop ran.
    PROVIDER_UNAVAILABLE  exit 2   no nonce, AND the transcript carries a
                                   signal that can only originate at the
                                   provider/transport layer (billing,
                                   quota, rate limit, auth rejection,
                                   upstream 5xx, connection failure).
                                   NOT a goose verdict. Not a pass either.
    FAIL                  exit 1   no nonce and no provider signal: goose
                                   was driven and the loop did not close.
                                   THIS is the version verdict.

ORDERING IS LOAD-BEARING: the nonce is checked FIRST. A transcript that
carries a rate-limit warning on one turn and still echoes the nonce on a
later turn is a PASS -- direct evidence of success outranks a symptom of
a difficulty that was survived. Reversing this order would let a
transient 429 downgrade a green run to INCONCLUSIVE.

The patterns below are deliberately PHRASE-anchored rather than
keyword-anchored. A bare `429` or a bare `credits` would also match the
model's own prose, and a classifier that can be talked into
PROVIDER_UNAVAILABLE by the text it is reading is how a real goose
regression gets excused as an outage -- the exact inversion of the bug
this file fixes. tests/test_canary_classify_smoke.py holds the negative
controls that keep it honest.
"""

from __future__ import annotations

import argparse
import re
import sys

VERDICT_PASS = "PASS"
VERDICT_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
VERDICT_FAIL = "FAIL"

EXIT_CODES = {
    VERDICT_PASS: 0,
    VERDICT_FAIL: 1,
    VERDICT_PROVIDER_UNAVAILABLE: 2,
}

# Each entry: (short label, compiled pattern).
# A pattern earns its place here only if it CANNOT be produced by goose's
# own recipe/extension/tool machinery -- i.e. it names something that
# happened on the far side of the HTTP call.
PROVIDER_SIGNALS: list[tuple[str, re.Pattern[str]]] = [
    # The literal 2026-08-31 line. Cerebras/OpenAI-compatible billing refusal.
    ("billing:add-more-credits", re.compile(
        r"add more credits", re.I)),
    ("billing:insufficient-quota", re.compile(
        r"insufficient[_ ](?:quota|credit|credits|balance|funds)", re.I)),
    ("billing:exceeded-quota", re.compile(
        r"(?:exceeded|exhausted)\s+your\s+(?:current\s+)?(?:quota|credits?|balance)", re.I)),
    ("billing:payment-required", re.compile(
        r"\b402\b|payment[_ ]required", re.I)),
    # Rate limiting. Anchored on the HTTP status *with* context, or on the
    # provider's own error code string -- never on a bare number.
    ("ratelimit:429", re.compile(
        r"(?:status(?:\s+code)?[:= ]+429\b"
        r"|\b429\s+too\s+many\s+requests"
        r"|http(?:\s+error)?[:= ]*429\b)", re.I)),
    ("ratelimit:code", re.compile(
        r"rate[_ ]limit(?:_exceeded|ed|\s+reached|\s+exceeded)", re.I)),
    ("ratelimit:tpm-rpm", re.compile(
        r"\b(?:requests|tokens)\s+per\s+(?:minute|day)\b.*\b(?:limit|exceeded)\b", re.I)),
    # Authentication/authorisation refused by the provider. A rotated or
    # unset key is an ops problem, never a goose-version problem.
    ("auth:401", re.compile(
        r"(?:status(?:\s+code)?[:= ]+401\b|\b401\s+unauthorized)", re.I)),
    ("auth:403", re.compile(
        r"(?:status(?:\s+code)?[:= ]+403\b|\b403\s+forbidden)", re.I)),
    ("auth:invalid-key", re.compile(
        r"(?:invalid[_ ]api[_ ]key|incorrect\s+api\s+key|api\s+key\s+not\s+valid"
        r"|authentication[_ ]error)", re.I)),
    # Upstream is down. Distinguish from our own bridge failing to start,
    # which the namespacing step owns and must keep owning.
    ("upstream:5xx", re.compile(
        r"(?:status(?:\s+code)?[:= ]+(?:500|502|503|504)\b"
        r"|\b(?:502\s+bad\s+gateway|503\s+service\s+unavailable|504\s+gateway\s+time)"
        r"|server[_ ]had[_ ]an[_ ]error|overloaded_error)", re.I)),
    ("transport:unreachable", re.compile(
        r"(?:connection\s+(?:refused|reset|closed\s+before)"
        r"|failed\s+to\s+(?:connect|lookup\s+address)"
        r"|dns\s+error"
        r"|(?:request|connection)\s+timed?\s*out"
        r"|error\s+sending\s+request\s+for\s+url)", re.I)),
]


def classify(transcript: str, nonce: str) -> tuple[str, str]:
    """Return (verdict, evidence-line).

    `evidence` is the actual matched text, never a restatement of it --
    R5: publish the basis with the number.
    """
    if not nonce:
        raise ValueError("nonce must be a non-empty string")

    marker = f"CANARY_TOOLCALL_OK::{nonce}"
    if marker in transcript:
        return VERDICT_PASS, marker

    for label, pattern in PROVIDER_SIGNALS:
        match = pattern.search(transcript)
        if match:
            line = _line_containing(transcript, match.start())
            return VERDICT_PROVIDER_UNAVAILABLE, f"{label}: {line}"

    return VERDICT_FAIL, "no nonce and no provider-layer signal in the transcript"


def _line_containing(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    if end == -1:
        end = len(text)
    return text[start:end].strip()[:300]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--transcript", required=True,
                    help="path to the tee'd goose stdout/stderr")
    ap.add_argument("--nonce", required=True,
                    help="the nonce the recipe was asked to echo")
    args = ap.parse_args(argv)

    try:
        with open(args.transcript, encoding="utf-8", errors="replace") as fh:
            transcript = fh.read()
    except OSError as exc:
        # A missing transcript is itself an unknown, not a goose verdict:
        # goose never got far enough to write one.
        print(f"CANARY_SMOKE_VERDICT::{VERDICT_PROVIDER_UNAVAILABLE}"
              f"::transcript unreadable: {exc}")
        return EXIT_CODES[VERDICT_PROVIDER_UNAVAILABLE]

    verdict, evidence = classify(transcript, args.nonce)
    print(f"CANARY_SMOKE_VERDICT::{verdict}::{evidence}")
    return EXIT_CODES[verdict]


if __name__ == "__main__":
    sys.exit(main())
