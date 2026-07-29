"""Salvage architect directives the harness discarded.

The architect repeatedly emits WELL-FORMED directive objects in fenced code
blocks but never reaches the propose_directive tool call, so run_goose_cycle()
sees proposed_delta<=0, logs the transcript tail, and THROWS THE WORK AWAY. On
2026-07-29 that happened on 3 of 4 cycles while the builder sat idle and the
starvation floor declared the gaps map EXHAUSTED -- the queue was empty not
because there was nothing to build but because the harness discarded what the
architect had already produced.

Same class as the builder-side prose salvage (goose 1.43 TOOL:-as-prose) and
FU-122 (valid calls inside fenced blocks): the model CONVERGED, the harness
discarded it. This recovers the content instead of re-deriving it.

Fires ONLY when delta<=0 -- i.e. only over a transcript that was already a
total loss. It cannot displace or race a converged tool call.
"""
import json
import re

VALID_HANDLERS = {"build_service", "generate_file"}
# A thin description GHOSTS: goose builds from `description` and nothing else.
# The starvation floor learned this the expensive way; hold salvage to the bar.
MIN_DESC = 80
MAX_PER_CYCLE = 5

_FENCE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)


def _candidate_blobs(text):
    """Fenced blocks first, then any balanced top-level {...} run."""
    seen = []
    for m in _FENCE.finditer(text or ""):
        seen.append(m.group(1).strip())
    # Bare objects (the model sometimes drops the fence entirely).
    depth = 0
    start = None
    for i, ch in enumerate(text or ""):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    seen.append(text[start:i + 1])
                    start = None
    return seen


def extract_directives(text):
    """Parse candidate directive dicts out of a goose transcript. No side effects."""
    out = []
    for blob in _candidate_blobs(text):
        try:
            d = json.loads(blob)
        except Exception:
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if not isinstance(it, dict):
                continue
            task = str(it.get("task") or "").strip()
            handler = str(it.get("handler") or "").strip()
            desc = str(it.get("description") or "").strip()
            if not task or handler not in VALID_HANDLERS:
                continue
            if len(desc) < MIN_DESC:
                continue
            # generate_file with no target is a no-op at the builder seam
            # (edit-class directives with output_file:null build nothing).
            if handler == "generate_file" and not str(it.get("output_file") or "").strip():
                continue
            out.append(it)
    uniq, seen_tasks = [], set()
    for it in out:
        t = it["task"].strip()
        if t in seen_tasks:
            continue
        seen_tasks.add(t)
        uniq.append(it)
    return uniq


def salvage(text, queued_stems, existing_files, stamp, writer, log=None,
            max_per_cycle=MAX_PER_CYCLE):
    """Write salvaged directives via `writer(filename, payload_dict)`.

    queued_stems   -- task names already in flight/finished (dedup set)
    existing_files -- output_file names already on disk (do not rebuild)
    Returns the number written.
    """
    written = 0
    for it in extract_directives(text):
        if written >= max_per_cycle:
            break
        task = it["task"].strip()
        if task in queued_stems:
            if log:
                log("salvage: skip %s (already queued/built)" % task)
            continue
        outf = str(it.get("output_file") or "").strip()
        if outf and outf in existing_files:
            if log:
                log("salvage: skip %s (%s already exists)" % (task, outf))
            continue
        payload = {
            "task": task,
            "handler": it["handler"],
            "complexity": it.get("complexity") or "medium",
            "priority": 0.75,
            "description": it["description"].strip(),
            "rationale": (
                "SALVAGED from the architect transcript at %s: the model emitted "
                "this directive but never reached propose_directive, so the "
                "harness would otherwise have discarded it (proposed_delta<=0)."
                % stamp
            ),
            "next_directive": {},
        }
        if outf:
            payload["output_file"] = outf
        if it.get("reads"):
            payload["reads"] = it["reads"]
        writer("salvage_%s_%s.json" % (stamp, task), payload)
        written += 1
        if log:
            log("SALVAGED directive: %s (handler=%s)" % (task, it["handler"]))
    return written
