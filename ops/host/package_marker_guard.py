#!/usr/bin/env python3
"""package_marker_guard.py -- outcome-based guard on the repo-root package markers.

WHY THIS EXISTS
  2026-08-13..08-16 the build pipeline was down for three days because
  zo_sentinel/__init__.py was mutated from a bare package marker into an eager
  import hub (GH issue #3415). It was repaired by hand on 08-16. By 2026-08-22
  it had regressed: the working tree again carried an "Auto-emitted service
  package" body with ~20 `from .X import Y` lines naming modules that do not
  exist, so `import zo_sentinel` raised ModuleNotFoundError and every
  `python3 -m zo_sentinel.*` entrypoint was dead on next start. The running
  promoter survived only because it held the pre-mutation module in memory --
  a restart would have re-opened the outage.

  The writer has not been identified. That is precisely why this guard tests an
  OUTCOME (does the package import?) rather than policing a writer. R7: prefer
  RECOVERY over RESTRICTION.

WHAT IT DOES  (idempotent; safe to run every watchdog tick)
  1. Probe: `import zo_sentinel` and `import app` in a SUBPROCESS.
     A subprocess, not an inline import -- an inline import caches on first
     success and freezes the verdict (FU-290).
  2. If a probe fails AND the marker differs from HEAD, snapshot the mutated
     file to _marker_forensics/<date>/ (forensics before repair), then
     `git checkout HEAD -- <marker>` and re-probe.
  3. Emit one machine-readable line per run to
     /home/workspace/logs/package_marker_guard.log.

EXIT CODES
  0  both markers import (either already clean, or repaired successfully)
  1  a probe still fails after repair -- a human/lane must look
  2  a probe failed and the marker was NOT dirty vs HEAD, so the fault is
     elsewhere and this guard must not pretend to have fixed it

WHAT WOULD SHOW THIS GUARD IS WRONG
  Run it against a deliberately mutated marker: it must exit 0 and leave the
  file clean. Run it against a clean tree with a genuinely missing dependency:
  it must exit 2 and change nothing. Both are exercised by --selftest.
"""
from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MARKERS = {"zo_sentinel": "zo_sentinel/__init__.py", "app": "app/__init__.py"}
LOG = "/home/workspace/logs/package_marker_guard.log"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(payload: dict) -> None:
    payload["ts"] = _now()
    line = json.dumps(payload, sort_keys=True)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def probe(pkg: str) -> tuple[bool, str]:
    """Import pkg in a SUBPROCESS so the verdict is re-observed every call."""
    p = subprocess.run([sys.executable, "-c", "import %s" % pkg],
                       cwd=REPO, capture_output=True, text=True, timeout=120)
    return p.returncode == 0, (p.stderr or "").strip().splitlines()[-1:] and \
        (p.stderr or "").strip().splitlines()[-1] or ""


def dirty(path: str) -> bool:
    p = subprocess.run(["git", "status", "--porcelain", "--", path],
                       cwd=REPO, capture_output=True, text=True, timeout=60)
    return bool(p.stdout.strip())


def repair(path: str) -> None:
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    dest_dir = os.path.join(REPO, "_marker_forensics", day)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, path.replace("/", "__") + ".mutated." + _now().replace(":", ""))
    try:
        shutil.copy2(os.path.join(REPO, path), dest)
    except OSError:
        dest = "<snapshot failed>"
    subprocess.run(["git", "checkout", "HEAD", "--", path],
                   cwd=REPO, capture_output=True, text=True, timeout=120)
    _log({"event": "repaired", "marker": path, "forensics": dest})


def main() -> int:
    worst = 0
    for pkg, path in MARKERS.items():
        ok, err = probe(pkg)
        if ok:
            _log({"event": "ok", "pkg": pkg})
            continue
        if not dirty(path):
            _log({"event": "fail_not_dirty", "pkg": pkg, "marker": path, "err": err,
                  "note": "probe RED but marker matches HEAD -- fault is elsewhere, not repairing"})
            worst = max(worst, 2)
            continue
        _log({"event": "red_and_dirty", "pkg": pkg, "marker": path, "err": err})
        repair(path)
        ok2, err2 = probe(pkg)
        _log({"event": "post_repair", "pkg": pkg, "ok": ok2, "err": err2})
        if not ok2:
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
