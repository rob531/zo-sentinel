"""serving_model_allowlist.py -- flag-gated serving allowlist for mcp_llm_axis_scores.

THE HAZARD this fixes (POSTMORTEM_20261030, a ~9%-sub incident; see memories
serving-read-is-latest-scored-at-not-pinned / verify-the-consumer-before-writing):
the per-server axis serving read picks a row per axis by NEWEST `scored_at`
(`row_number() OVER (partition_by=axis_name, order_by=scored_at DESC)` in
axis_scores_query_api.get_server_axis_summary) and does NOT consult any
`active_model_config` / `cutover_armed` / serving_version. There is no
active_model_config table in this repo. So ANY current-timestamp write to
mcp_llm_axis_scores under a fresh `scored_at` -- a partial-axis live probe, an
experimental model_version, a v6 rescore -- silently becomes "latest" and hijacks
the served product for every server it touches. That is exactly what degraded the
live overview for 1541 servers on 2026-10-30.

THE FIX (the "pick latest among FULL, armed models only" option from the corrected
plan): an explicit, operator-controlled SERVING allowlist of the model_versions that
are permitted to be served. The serving query, when the allowlist is ON, filters to
rows whose model_version is on the allowlist BEFORE it picks latest-per-axis. A
partial or experimental model_version that is NOT on the allowlist can never win on
timestamp alone -- writing it is no longer a live serving change.

Design mirrors unauth_access.py (the chairman-blessed resolver convention for this
same table):
  * OPT-IN, DEFAULT OFF. Unset / empty SERVING_FULL_MODEL_ALLOWLIST -> returns None
    -> the serving query is UNCHANGED (today's newest-scored_at-per-axis behavior).
    This is why the PR is a safe no-op until the operator arms it. Arming (choosing
    which model_versions are "full / served") is a metric/verdict decision = HELD.
  * EXPLICIT, not inferred. The allowlist is the set of model_versions an operator
    has declared servable, supplied as a comma/space-separated env list. No heuristic
    guesses which versions are "full" -- that would reintroduce the ambiguity the
    incident came from.
  * FAIL-OPEN but NOT SILENT is not relevant here: parsing an env string cannot touch
    the DB and cannot raise on a doomed transaction. A malformed/empty entry is simply
    dropped; if that leaves the list empty the function returns None (OFF), never an
    empty allowlist that would serve nothing.
"""
from __future__ import annotations

import os
from typing import List, Optional

_ENV = "SERVING_FULL_MODEL_ALLOWLIST"


def serving_model_allowlist() -> Optional[List[str]]:
    """The operator-armed set of model_versions permitted to be served, or None.

    None means the feature is OFF -> callers MUST fall back to today's unfiltered
    (newest-scored_at-per-axis) behavior, so the serving read is byte-for-byte
    unchanged until an operator arms the allowlist.

    A non-empty list means ON -> callers restrict the serving read to rows whose
    model_version is IN this list before picking latest-per-axis.

    Parsing: split on comma AND whitespace, strip, drop empties, de-dupe preserving
    order. An env value that is unset, empty, or only separators yields None (OFF) --
    never an empty list, which would serve nothing.
    """
    raw = os.environ.get(_ENV)
    if raw is None:
        return None
    tokens: List[str] = []
    seen = set()
    for part in raw.replace(",", " ").split():
        part = part.strip()
        if part and part not in seen:
            seen.add(part)
            tokens.append(part)
    return tokens or None


if __name__ == "__main__":  # hermetic self-test (no DB, no network)
    # Pole 1 -- OFF by default (unset) and OFF for empties -> None (fall back to today's read)
    os.environ.pop(_ENV, None)
    assert serving_model_allowlist() is None, "unset must be OFF (None)"
    for empty in ("", "   ", ",", " , , "):
        os.environ[_ENV] = empty
        assert serving_model_allowlist() is None, f"{empty!r} must be OFF (None), never []"

    # Pole 2 -- ON: parses a real allowlist (comma and/or whitespace), de-dupes, order-preserving
    os.environ[_ENV] = "v5_prod, v3.4_prod"
    assert serving_model_allowlist() == ["v5_prod", "v3.4_prod"], serving_model_allowlist()
    os.environ[_ENV] = "v5_prod v3.4_prod"
    assert serving_model_allowlist() == ["v5_prod", "v3.4_prod"], serving_model_allowlist()
    os.environ[_ENV] = "v5_prod, v5_prod , v3.4_prod"
    assert serving_model_allowlist() == ["v5_prod", "v3.4_prod"], "must de-dupe"
    os.environ[_ENV] = "  lpg_probe_v1  "
    assert serving_model_allowlist() == ["lpg_probe_v1"], "single entry"

    print("PASS")
