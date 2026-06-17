#!/usr/bin/env python3
# DEPLOY SHIM (added 2026-06-14).
#
# The daemon launcher and tools/reload_daemon.sh execute THIS top-level path:
#   /home/workspace/zo_sentinel/sentinel_directive_generator_goose.py
# but the maintained source lives in the package dir:
#   zo_sentinel/sentinel_directive_generator_goose.py
# Before this shim, the top-level path on the box was an UNTRACKED stale copy,
# so generator changes merged to the tracked file (e.g. the graph-aware dedup +
# idle-gate, and the ctx/timeout fix) were deployed by refresh_code but NEVER
# reached the running daemon -- it kept executing the stale top-level copy.
#
# This shim makes the top-level path a thin, TRACKED delegator to the package
# module, so `refresh_code` + `reload_daemon sentinel_directive_generator_goose`
# always run the current maintained source. Generator-only; no builder/publisher
# impact.
import runpy

runpy.run_path(
    "/home/workspace/zo_sentinel/zo_sentinel/sentinel_directive_generator_goose.py",
    run_name="__main__",
)
