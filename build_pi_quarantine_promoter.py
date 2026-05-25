#!/usr/bin/env python3
"""
build_pi_quarantine_promoter.py -- ORPHAN FILE, DO NOT RUN

This file was accidentally created on 2026-04-16 20:28 UTC when the builder
processed a chain directive from the original pi_corpus_ingest rebuild. The
chain name 'build_pi_quarantine_promoter' was taken literally as the output
filename (with 'build_' prefix preserved). The file contained an interactive
CLI promoter pattern that was explicitly redesigned away in favour of the
automated review layer (pi_quarantine_reviewer + pi_quarantine_promoter).

The correct files are:
  - /home/workspace/zo_sentinel/pi_quarantine_reviewer.py   (built 20:34 UTC)
  - /home/workspace/zo_sentinel/pi_quarantine_promoter.py   (built 20:35 UTC)
  - /home/workspace/zo_sentinel/pi_flagged_review_api.py    (built 20:37 UTC)

Original content preserved by the writer tool's auto-backup as
.bak.<timestamp>. Do not run this stub. See PROMPT_INJECTION_PLAN.md
incident log for context.
"""
import sys

REFUSAL_MESSAGE = (
    "build_pi_quarantine_promoter.py is an ORPHAN from a chain-directive "
    "filename bug. Use pi_quarantine_promoter.py instead. See docstring."
)

def main():
    print(REFUSAL_MESSAGE, file=sys.stderr)
    sys.exit(2)

if __name__ == '__main__':
    main()