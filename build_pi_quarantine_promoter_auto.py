#!/usr/bin/env python3
"""
build_pi_quarantine_promoter_auto.py -- ORPHAN FILE, DO NOT RUN

This file was accidentally created on 2026-04-16 20:42 UTC when a stale
chain reference re-fired the 'build_pi_quarantine_promoter_auto' task name
as a literal output filename. The content is a half-finished duplicate of
the real pi_quarantine_promoter.py with added stray FastAPI routes.

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
    "build_pi_quarantine_promoter_auto.py is an ORPHAN from a chain-directive "
    "filename bug. Use pi_quarantine_promoter.py instead. See docstring."
)

def main():
    print(REFUSAL_MESSAGE, file=sys.stderr)
    sys.exit(2)

if __name__ == '__main__':
    main()