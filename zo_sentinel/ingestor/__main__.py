"""
CLI for the artifact ingestor (host-side; talks to write_service).

    python -m zo_sentinel.ingestor status        # enabled? watermark? home?
    python -m zo_sentinel.ingestor run-once       # one cycle (dry-run if dormant)
    python -m zo_sentinel.ingestor run [--interval 300]   # daemon loop

Activation (all default OFF): ARTIFACT_INGESTOR_ENABLED=1, a `.ingestor_enabled`
sentinel in $ZO_SENTINEL_HOME, or run with --enable. While dormant it only
reports what it WOULD promote/quarantine; it writes nothing.
"""
from __future__ import annotations

import argparse
import json
import sys

from zo_sentinel.ingestor.governor import AutoActivationGovernor
from zo_sentinel.ingestor.ingestor import ArtifactIngestor
from zo_sentinel.ingestor.store import HttpMeshStore


def _make(args) -> ArtifactIngestor:
    enabled = True if getattr(args, "enable", False) else None
    return ArtifactIngestor(HttpMeshStore(), enabled=enabled)


def _governor(args) -> AutoActivationGovernor:
    # Host-side: HttpMeshStore + DuckDB gate_8 oracle (the governor's defaults).
    return AutoActivationGovernor(ArtifactIngestor(HttpMeshStore()),
                                  auto=not getattr(args, "propose", False))


def cmd_status(args) -> int:
    print(json.dumps(_make(args).status(), indent=2))
    return 0


def cmd_run_once(args) -> int:
    ing = _make(args)
    verdicts = ing.run_once()
    mode = "ACTED" if ing.is_enabled() else "DRY-RUN (dormant)"
    print(f"[{mode}] {len(verdicts)} artifact(s)")
    for v in verdicts:
        flag = "PROMOTE" if v.ok else ("QUARANTINE/SAFETY" if v.safety_block else "QUARANTINE")
        print(f"  {flag:<18} {v.artifact.file}  [{v.contract}] {v.detail[:80]}")
    return 0


def cmd_run(args) -> int:
    _make(args).run_forever(interval_sec=args.interval)
    return 0


def cmd_govern(args) -> int:
    print(json.dumps(_governor(args).run_once(), indent=2))
    return 0


def cmd_govern_status(args) -> int:
    print(json.dumps(_governor(args).status(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m zo_sentinel.ingestor",
                                description="net-new code-artifact ingestor")
    p.add_argument("--enable", action="store_true",
                   help="force-activate this invocation (otherwise dormant)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("run-once").set_defaults(func=cmd_run_once)
    r = sub.add_parser("run"); r.add_argument("--interval", type=int, default=300)
    r.set_defaults(func=cmd_run)
    # auto-activation governor (decides when to flip the ingestor live)
    g = sub.add_parser("govern", help="run one auto-activation governance cycle")
    g.add_argument("--propose", action="store_true",
                   help="propose-only: assess readiness but don't write the latch")
    g.set_defaults(func=cmd_govern)
    sub.add_parser("govern-status").set_defaults(func=cmd_govern_status)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
