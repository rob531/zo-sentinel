#!/usr/bin/env python3
"""
breaker_action_investigate_enrichment_coverage_diagnostic.py
=============================================================

Quality-gate breaker action ``investigate`` for the quarantined diagnostic
``enrichment_coverage_diagnostic.py``.

Context (set by directive_architect at 2026-06-17T14:06:03.206038+00:00)
-------------------------------------------------------------------------
* ``enrichment_coverage_diagnostic.py`` was quarantined on 2026-06-17T08:53
  after **4 consecutive quality-gate failures**.
* Backed-table cardinality gap detected by sentinel-health:
    - ``mcp_signal_enrichments`` : **12 rows**
    - ``mcp_signal_scores``      : **1,190,000 rows**
* This represents a near-complete enrichment-pipeline stall. The
  diagnostic file is the canonical tool for explaining that gap, so
  unblocking it has the highest expected information value.

Action semantics
----------------
This file is *not* a rebuild of the quarantined diagnostic.  It is a
breaker-system action that runs an investigation harness when invoked
by the quality-gate breaker orchestrator.  The action:

1. Verifies the quarantined file still exists and matches its expected
   quarantine manifest.
2. Inspects the surrounding enrichment pipeline for the three failure
   modes hypothesised in the directive:
       a. missing ``enrichment_harness.py`` wiring,
       b. ``enrichments_writer_daemon`` not consuming correctly,
       c. missing ``write_service`` integration.
3. Queries live counts on the two backed tables (if reachable) to confirm
   the staleness signal.
4. Emits a structured investigation report that downstream breaker
   actions (``quarantine``, ``rebuild``, ``dispatch_repair`` …) can
   consume.

The module exposes a single public entry point:
    :func:`run_breaker_action` -> :class:`InvestigationReport`
and a CLI entry point:
    ``python breaker_action_investigate_enrichment_coverage_diagnostic.py``

The module is intentionally side-effect-light outside of the
investigation report and the optional quarantine-manifest write so it
can be safely re-run by the breaker orchestrator.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import logging
import os
import socket
import sqlite3
import sys
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

ACTION_NAME = "investigate"
TARGET_FILE_RELATIVE = Path("breaker_actions") / Path(__file__).name  # self-ref
QUARANTINED_TARGET = Path("diagnostics") / "enrichment_coverage_diagnostic.py"
QUARANTINE_MANIFEST = Path("diagnostics") / ".quarantine"