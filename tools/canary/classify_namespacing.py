#!/usr/bin/env python3
"""Classify a goose-canary stdio-namespacing transcript.

WHY THIS EXISTS (chairman issue #4077, run 35426553926, 2026-09-19)
-------------------------------------------------------------------
The namespacing probe lived as inline bash in goose-canary.yml. It had
four branches and the third one was unfalsifiable:

    if grep -q "$VERBATIM" /tmp/namespacing.out; then
      echo "PROBE INCONCLUSIVE -- NOT A NAMESPACING VERDICT."
      ...
      exit 1

`$VERBATIM` is `zo_directive_bridge__read_protected_files`, and goose
prints the recipe's own **Description** into the transcript before it
does anything at all -- a description whose text names that tool
verbatim. So that grep matches on EVERY run, including a run where the
bridge never resolved a thing. The branch could not distinguish any two
worlds, and on 2026-09-19 it published `PROBE INCONCLUSIVE` and exited 1
on a transcript that contained goose's own dispatch render:

      > read_protected_files zo_directive_bridge

i.e. DIRECT evidence that the name resolved and the tool was invoked.
The probe failed the build on the strength of the thing it was built to
confirm. This is the same loss the `-32602` harvest already fixed once in
this file's bash ancestor ("the error line is POSITIVE evidence of
correct namespacing and was being thrown away. We harvest it.") -- the
dispatch render was the second piece of evidence on the floor.

What actually killed the turn was one line further down, and it is not a
goose verdict either:

    400: 'messages.2' : for 'role:assistant' ... property
         'reasoning_content' is unsupported

goose replays `reasoning_content` across tool-call turns (upstream
#10366) and the provider rejects the property. The workflow carried a
comment asserting this was avoided by driving the step on groq rather
than cerebras. Run 35426553926 disproves that comment in one line: the
session header reads `openai openai/gpt-oss-120b` -- it WAS on groq --
and it died identically. `gpt-oss` emits reasoning on either rung. The
mitigation was never a mitigation; it was an untested belief, and it is
corrected in the workflow alongside this file.

CONTRACT
--------
    classify(transcript, nonce) -> (verdict, evidence)

    PASS_DIRECT      exit 0  NAMESPACING_OK::<nonce> was echoed. The
                             whole loop closed.
    PASS_DISPATCHED  exit 0  the nonce never arrived, but the transcript
                             carries proof goose RESOLVED the verbatim
                             name: it either named the tool in a -32602
                             dispatcher error or rendered its own
                             dispatch line for it. Namespacing intact;
                             whatever killed the turn is a DIFFERENT
                             defect and must not be reported as a rename.
    INCONCLUSIVE     exit 2  no resolution evidence, but a provider-layer
                             signal is present (billing, quota, rate
                             limit, auth, 5xx, transport, or the
                             reasoning_content replay rejection). NOT a
                             goose verdict, and NOT a pass.
    FAIL             exit 1  EXTENSION_START_FAILURE, an explicit
                             tool-not-found, or nothing at all. THIS is
                             the version verdict -- the 1.38 starvation
                             shape. Do not flip.

ORDERING IS LOAD-BEARING, and differently from classify_smoke.py:

  1. EXTENSION_START_FAILURE is checked FIRST. goose does not abort when
     an stdio extension fails to start -- it warns and runs on without
     it. A session with no bridge is the starvation shape arriving by a
     different road and must still fail, so no later branch may excuse
     it.
  2. TOOL-NOT-FOUND is checked BEFORE any resolution evidence. This is
     the one that makes PASS_DISPATCHED falsifiable at all: a
     `Tool not found: zo_directive_bridge__read_protected_files` line
     CONTAINS the verbatim name, so a dispatch-evidence check that ran
     first would read a genuine rename as a pass -- turning a gate that
     publishes false reds into one that publishes false greens, which is
     strictly worse. tests/test_canary_classify_namespacing.py holds
     that exact transcript as a negative control.
  3. Direct evidence outranks dispatcher evidence outranks a provider
     signal, for the classify_smoke.py reason: evidence that the loop
     CLOSED outranks a symptom of a difficulty that was survived.

The recipe preamble is SCRUBBED before any name matching. Every line
goose prints from the recipe's own `Description:` names the verbatim
tool; matching on those is how the branch this file replaces became
unfalsifiable. A classifier that can be satisfied by the text it was
handed is not reading the world.
"""

from __future__ import annotations

import argparse
import importlib.util
import pathlib
import re
import sys

VERDICT_PASS_DIRECT = "PASS_DIRECT"
VERDICT_PASS_DISPATCHED = "PASS_DISPATCHED"
VERDICT_INCONCLUSIVE = "INCONCLUSIVE"
VERDICT_FAIL = "FAIL"

EXIT_CODES = {
    VERDICT_PASS_DIRECT: 0,
    VERDICT_PASS_DISPATCHED: 0,
    VERDICT_FAIL: 1,
    VERDICT_INCONCLUSIVE: 2,
}

# The name directive_architect.yaml hardcodes. Split form is what goose
# renders when it dispatches: "<tool> <extension>".
EXTENSION = "zo_directive_bridge"
TOOL = "read_protected_files"
VERBATIM = f"{EXTENSION}__{TOOL}"

_EXTENSION_START_FAILURE = re.compile(
    rf"Failed to start extension '{re.escape(EXTENSION)}'", re.I)

# The 1.38 starvation shape, stated by goose rather than inferred. These
# lines may THEMSELVES carry the verbatim name, which is why they are
# checked before any name-based resolution evidence.
_TOOL_NOT_FOUND = re.compile(
    r"(?:tool\s+not\s+found"
    r"|no\s+such\s+tool"
    r"|unknown\s+tool"
    r"|tool\s+'[^']*'\s+(?:was\s+)?not\s+found"
    r"|-32601)", re.I)

# goose can only print this if it RESOLVED the name.
_DISPATCHER_ERROR = re.compile(
    rf"-32602:\s*Tool arguments for {re.escape(VERBATIM)}", re.I)

# goose's own dispatch render, in either the joined or the split form.
_DISPATCH_JOINED = re.compile(rf"\b{re.escape(VERBATIM)}\b")
_DISPATCH_SPLIT = re.compile(
    rf"\b{re.escape(TOOL)}\b.*\b{re.escape(EXTENSION)}\b"
    rf"|\b{re.escape(EXTENSION)}\b.*\b{re.escape(TOOL)}\b")

# Lines goose echoes out of the recipe FILE rather than out of the world.
# The probe recipe's description names the verbatim tool, so every one of
# these lines is a free match for any name pattern.
_RECIPE_PREAMBLE = re.compile(
    r"^\s*(?:Loading recipe:"
    r"|Description:"
    r"|Parameters used to load this recipe:"
    r"|\s*nonce:"
    r"|Scar #454)", re.I)
# The description is one long wrapped line in some renderings; key on its
# own signature rather than on position.
_RECIPE_PROSE = re.compile(r"Scar #454|the VERBATIM name the architect recipe", re.I)


def _load_provider_signals() -> list[tuple[str, "re.Pattern[str]"]]:
    """Reuse classify_smoke.py's PROVIDER_SIGNALS -- one list, not two.

    A second copy would drift, and a provider signal that is known to one
    canary step and unknown to the next is how the same outage reads as
    an outage in one line and a version verdict in the next.
    """
    path = pathlib.Path(__file__).resolve().parent / "classify_smoke.py"
    spec = importlib.util.spec_from_file_location("_canary_classify_smoke", path)
    if not (spec and spec.loader):  # pragma: no cover - packaging accident
        return []
    module = importlib.util.module_from_spec(spec)
    sys.modules["_canary_classify_smoke"] = module
    spec.loader.exec_module(module)
    return list(module.PROVIDER_SIGNALS)


# The replay rejection is specific to this step (the smoke step never
# replays an assistant turn), but it is a provider-layer 400 like any
# other: it happens on the far side of the HTTP call and goose's recipe
# machinery cannot produce it.
PROVIDER_SIGNALS: list[tuple[str, "re.Pattern[str]"]] = [
    ("replay:reasoning-content-unsupported", re.compile(
        r"property\s+'?reasoning_content'?\s+is\s+unsupported", re.I)),
] + _load_provider_signals()


def scrub_recipe_preamble(transcript: str) -> str:
    """Drop the lines goose echoes out of the recipe file itself."""
    kept = []
    for line in transcript.splitlines():
        if _RECIPE_PREAMBLE.search(line) or _RECIPE_PROSE.search(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def classify(transcript: str, nonce: str) -> tuple[str, str]:
    """Return (verdict, evidence-line).

    `evidence` is the matched text, never a restatement of it -- R5:
    publish the basis with the number.
    """
    if not nonce:
        raise ValueError("nonce must be a non-empty string")

    match = _EXTENSION_START_FAILURE.search(transcript)
    if match:
        return VERDICT_FAIL, (
            "EXTENSION_START_FAILURE: "
            + _line_containing(transcript, match.start()))

    body = scrub_recipe_preamble(transcript)

    match = _TOOL_NOT_FOUND.search(body)
    if match:
        return VERDICT_FAIL, (
            "RENAME (tool-not-found): " + _line_containing(body, match.start()))

    marker = f"NAMESPACING_OK::{nonce}"
    if marker in transcript:
        return VERDICT_PASS_DIRECT, marker

    match = _DISPATCHER_ERROR.search(body)
    if match:
        return VERDICT_PASS_DISPATCHED, (
            "dispatcher named the tool: " + _line_containing(body, match.start()))

    for line in body.splitlines():
        if _DISPATCH_JOINED.search(line) or _DISPATCH_SPLIT.search(line):
            return VERDICT_PASS_DISPATCHED, (
                "goose dispatched the tool: " + line.strip()[:300])

    for label, pattern in PROVIDER_SIGNALS:
        match = pattern.search(transcript)
        if match:
            return VERDICT_INCONCLUSIVE, (
                f"{label}: " + _line_containing(transcript, match.start()))

    return VERDICT_FAIL, (
        f"RENAME: {VERBATIM} appears nowhere outside the recipe preamble, "
        "the bridge started, and no provider-layer signal was present")


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
        # A missing transcript is an unknown, not a rename: goose never
        # got far enough to write one. R6.
        print(f"CANARY_NAMESPACING_VERDICT::{VERDICT_INCONCLUSIVE}"
              f"::transcript unreadable: {exc}")
        return EXIT_CODES[VERDICT_INCONCLUSIVE]

    verdict, evidence = classify(transcript, args.nonce)
    print(f"CANARY_NAMESPACING_VERDICT::{verdict}::{evidence}")
    return EXIT_CODES[verdict]


if __name__ == "__main__":
    sys.exit(main())
