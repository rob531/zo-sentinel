"""
zo_sentinel.ingestor -- the net-new code-artifact ingestor.

zo-sentinel builds itself: directive_factory/goose_runner dispatch build
directives, the t1.zo_sentinel_builder tier generates new modules/views/docs,
and each generated file is registered as a `build_artifact` row in mesh_memory.

This package is the single coherent ingestor for those net-new artifacts. It
subscribes to new `build_artifact` rows, runs gate_8-style contract + static
safety checks on each one INLINE, then:

    * PASS  -> promote   (record approval so the artifact can go live)
    * FAIL  -> quarantine + reverse-feed a fix-directive back into mesh_memory
               (agent_id=zo_sentinel.directive / memory_type=build_directive),
               which goose_runner picks up to regenerate -- closing the loop.

It is the host-side, mesh_memory-driven sibling of the GitHub PR smoke ladder
(tests/ci): same contract philosophy (import-smoke, html-form, safety scan),
two ingestion points.

Safety:
    * DORMANT by default -- run_once() is a read-only dry-run until explicitly
      activated (env ARTIFACT_INGESTOR_ENABLED / sentinel / ctor). It never
      writes promotions, quarantines, or directives while dormant.
    * the storage seam (store.py) is mockable, so the whole thing is hermetic
      and CI-gated; nothing here touches the live host at import time.
"""

from zo_sentinel.ingestor.model import (  # noqa: F401
    ArtifactType,
    BuildArtifact,
    IngestAction,
    IngestVerdict,
)

__all__ = ["ArtifactType", "BuildArtifact", "IngestAction", "IngestVerdict"]
