#!/usr/bin/env python3
"""vast_job_runner.py -- CLI over zo_sentinel.vast_jobs (the managed GPU-job
lifecycle). Every paid instance goes through this, never raw `vastai` calls:
the ledger + audit is what makes vast jobs visible to the E2E system.

  python3 tools/vast_job_runner.py run jobs/registry_rescore_v1.json
  python3 tools/vast_job_runner.py audit            # cron/scheduled-task daily
  python3 tools/vast_job_runner.py ledger [n]       # tail the run ledger
  python3 tools/vast_job_runner.py destroy <iid>    # manual (forensics first!)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from zo_sentinel import vast_jobs  # noqa: E402


def main(argv) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]
    if cmd == "run" and len(argv) >= 2:
        manifest = vast_jobs.load_manifest(argv[1])
        client = vast_jobs.RealVastClient()
        workdir = vast_jobs.state_dir() / "runs" / manifest["name"]
        summary = vast_jobs.run_job(manifest, client, workdir)
        print(json.dumps(summary, indent=2))
        return 0 if summary.get("verdict") == "DESTROY_READY" else 1
    if cmd == "audit":
        report = vast_jobs.audit(vast_jobs.RealVastClient())
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    if cmd == "ledger":
        n = int(argv[1]) if len(argv) > 1 else 20
        for row in vast_jobs.ledger_rows()[-n:]:
            print(json.dumps(row))
        return 0
    if cmd == "destroy" and len(argv) >= 2:
        client = vast_jobs.RealVastClient()
        ok = client.destroy(argv[1])
        vast_jobs.ledger_append("manual_destroy", f"manual_{argv[1]}",
                                instance_id=argv[1], destroyed=bool(ok))
        print("DESTROYED" if ok else "DESTROY_FAIL")
        return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
