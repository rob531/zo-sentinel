#!/usr/bin/env python3
"""Bound a failure list by BYTES, because a bound by LINES does not bound one line.

friction family `line-count-bound-on-a-oneline-payload` (first bitten
2026-09-27; 4 hits in 1 lane in the 7 days to 2026-10-02T09:05:03Z):

    `generate_spine.py --check --strict --quiet` printed all 542 UNLISTED broken
    active service names on ONE line.  The caller's `| head -25` guard bounded
    NOTHING -- head, tail and Select-Object -First N all pass a single 10KB line
    through whole -- and ~8-10k tokens of the deploy lane's result budget went,
    three runs in a row, to a list that lane is explicitly instructed not to fix
    inline.

The consumer cannot defend itself: there is no line-count bound, in any shell,
that bounds a one-line payload.  So the PRODUCER bounds itself, by BYTES, here.

    from bounded import sample
    print("STRICT: %d UNLISTED broken service(s): %s"
          % (len(bad), sample(bad, where="artifacts/spine_manifest.json")))

HARNESS_DOCTRINE this honours:
  R5  the trailer publishes the BASIS -- how many were shown, of how many, under
      which byte cap.
  R6  withheld is UNKNOWN, not absent.  The withheld COUNT is always stated, so
      a bounded line can never be misread as a short list.
  R7  recovery over restriction.  `where` names the place the WHOLE list still
      lives, so bounding the line never destroys the evidence.

A short list renders byte-identically to the unbounded form (no trailer at all),
so adopting this is a no-op for every call site that was never going to flood.
"""

from __future__ import annotations

# 400 bytes: the longest *useful* inline sample. Basis: the three bites above
# each put 8-10KB on one line; 400B is ~20 service names, enough to recognise
# the population, and ~1/25th of the smallest observed flood.
SAMPLE_BYTES = 400

# Hard ceiling on any single rendered element, so one pathological item cannot
# defeat the cap on its own.
ITEM_BYTES = 120

_ELLIPSIS = "..."


def _bytelen(s: str) -> int:
    return len(s.encode("utf-8", "replace"))


def clip(s: str, cap: int = ITEM_BYTES) -> str:
    """Truncate one string to `cap` BYTES, marking that it was truncated."""
    if cap <= 0:
        return ""
    if _bytelen(s) <= cap:
        return s
    keep = max(0, cap - len(_ELLIPSIS))
    out = s.encode("utf-8", "replace")[:keep].decode("utf-8", "ignore")
    return out + _ELLIPSIS


def sample(items, cap: int = SAMPLE_BYTES, sep: str = ", ",
           where: str = "", render=None, item_cap: int = ITEM_BYTES) -> str:
    """Render `items` as ONE line bounded by BYTES, never by element count.

    Returns a string whose length is at most `cap` plus a short fixed-shape
    trailer.  An empty sequence renders as "(none)" -- never as "".
    """
    render = render or str
    items = list(items)
    if not items:
        return "(none)"

    shown: list[str] = []
    used = 0
    for it in items:
        s = clip(render(it), item_cap)
        add = _bytelen(s) + (len(sep) if shown else 0)
        if shown and used + add > cap:
            break
        shown.append(s)
        used += add
        if used >= cap:
            break

    withheld = len(items) - len(shown)
    out = sep.join(shown)
    if withheld:
        out += ("  [+%d of %d WITHHELD at the %dB cap -- withheld is UNKNOWN, "
                "not absent" % (withheld, len(items), cap))
        out += ("; whole list: %s]" % where) if where else "]"
    return out


def self_test() -> int:
    """Two poles. The NEGATIVE CONTROL is pole 1: the unbounded form goes RED."""
    big = ["svc_%04d=import_error" % i for i in range(542)]
    unbounded = ", ".join(big)
    bad = 0

    # POLE 1 (NEGATIVE CONTROL) -- the form this module replaces MUST flood.
    # If this ever passes, the cap below is measuring nothing.
    if _bytelen(unbounded) <= SAMPLE_BYTES:
        print("FAIL pole1: the unbounded join did NOT flood (%dB <= %dB cap); "
              "this assertion has never been red and is therefore UNPROVEN"
              % (_bytelen(unbounded), SAMPLE_BYTES))
        bad += 1
    else:
        print("ok   pole1 NEGATIVE CONTROL: unbounded join = %dB on one line (RED)"
              % _bytelen(unbounded))

    # POLE 2 -- the bounded form holds, and says what it withheld.
    got = sample(big, where="artifacts/spine_manifest.json")
    if "\n" in got:
        print("FAIL pole2: sample() emitted a newline")
        bad += 1
    if _bytelen(got) > SAMPLE_BYTES + 200:
        print("FAIL pole2: sample() = %dB, over cap+trailer" % _bytelen(got))
        bad += 1
    if "WITHHELD" not in got or "of 542" not in got:
        print("FAIL pole2: sample() did not publish the withheld basis: %r" % got[-120:])
        bad += 1
    if "artifacts/spine_manifest.json" not in got:
        print("FAIL pole2: sample() dropped the recovery pointer (R7)")
        bad += 1
    if bad == 0:
        print("ok   pole2 BOUNDED: %dB, trailer names 542 and the recovery path" % _bytelen(got))

    # POLE 3 -- a short list is byte-identical to the unbounded form (no churn).
    small = ["a=1", "b=2"]
    if sample(small) != ", ".join(small):
        print("FAIL pole3: a short list was altered: %r" % sample(small))
        bad += 1
    else:
        print("ok   pole3 NO-OP on a short list")

    # POLE 4 -- one pathological element cannot defeat the cap.
    if _bytelen(sample(["x" * 100000])) > SAMPLE_BYTES + 200:
        print("FAIL pole4: a single huge element defeated the cap")
        bad += 1
    else:
        print("ok   pole4 one huge element is clipped, not passed through")

    # POLE 5 -- empty is "(none)", never an empty string read as "no problem".
    if sample([]) != "(none)":
        print("FAIL pole5: empty rendered as %r, not '(none)'" % sample([]))
        bad += 1
    else:
        print("ok   pole5 empty renders as (none), not ''")

    print("bounded.py self_test: %s" % ("PASS" if bad == 0 else "FAIL (%d)" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    import sys
    sys.exit(self_test())
