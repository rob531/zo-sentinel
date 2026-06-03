"""
ingestor.py -- ArtifactIngestor: the orchestrator.

Pulls new `build_artifact` rows via the store, validates each with the gate_8
contracts, and (when activated) promotes passes / quarantines fails and
reverse-feeds a fix-directive for the failures so goose_runner regenerates.

Dormancy is the safety contract: while dormant, run_once() computes verdicts and
the action it WOULD take, but writes nothing. Activate with any of:
constructor enabled=True, env ARTIFACT_INGESTOR_ENABLED=1, or a `.ingestor_enabled`
sentinel file in the sentinel home.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from zo_sentinel.ingestor.contracts import classify, validate_file
from zo_sentinel.ingestor.model import (
    BuildArtifact,
    IngestAction,
    IngestVerdict,
)
from zo_sentinel.ingestor.store import (
    DIRECTIVE_AGENT_ID,
    BUILD_DIRECTIVE_TYPE,
    INGESTOR_AGENT_ID,
    HttpMeshStore,
    MeshStore,
)

SENTINEL_NAME = ".ingestor_enabled"
DEFAULT_HOME = "/home/workspace/zo_sentinel"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArtifactIngestor:
    def __init__(self, store: Optional[MeshStore] = None, *,
                 sentinel_home: Optional[str] = None,
                 enabled: Optional[bool] = None,
                 batch_limit: int = 40):
        self.store: MeshStore = store or HttpMeshStore()
        self.home = Path(sentinel_home or os.environ.get("ZO_SENTINEL_HOME", DEFAULT_HOME))
        self._enabled_override = enabled
        self.batch_limit = batch_limit

    # ---- activation latch (same shape as the SFT batch runner) -------------

    def is_enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        if os.environ.get("ARTIFACT_INGESTOR_ENABLED", "").strip() in ("1", "true", "yes"):
            return True
        return (self.home / SENTINEL_NAME).exists()

    def activation_reason(self) -> str:
        if self._enabled_override is True:
            return "constructor enabled=True"
        if self._enabled_override is False:
            return "constructor enabled=False (forced dormant)"
        if os.environ.get("ARTIFACT_INGESTOR_ENABLED", "").strip() in ("1", "true", "yes"):
            return "env ARTIFACT_INGESTOR_ENABLED"
        if (self.home / SENTINEL_NAME).exists():
            return f"sentinel {SENTINEL_NAME}"
        return "dormant (no activation latch open)"

    # ---- path resolution ---------------------------------------------------

    def _resolve(self, artifact: BuildArtifact) -> Path:
        p = Path(artifact.file)
        if p.is_absolute():
            return p
        return self.home / artifact.file

    # ---- reverse-feed: build a fix-directive for a failed artifact ---------

    def _fix_directive(self, verdict: IngestVerdict) -> dict:
        art = verdict.artifact
        stem = Path(art.file).stem
        kind = "security stop" if verdict.safety_block else "contract failure"
        return {
            "directive_id": f"fix_{stem}_{uuid.uuid4().hex[:8]}",
            "source": "artifact_ingestor",
            "origin": "artifact_ingestion_quarantine",
            "complexity": "medium",
            "file": art.file,
            "contract": verdict.contract,
            "description": (
                f"Regenerate {art.file}: {kind} in contract "
                f"'{verdict.contract}' -- {verdict.detail}. "
                f"Produce a version that satisfies the {verdict.artifact_type.value} "
                "contract and contains no forbidden mutations of protected core tables."
            ),
        }

    # ---- validate a single artifact ---------------------------------------

    def _is_quarantined(self, file: str) -> bool:
        """True if the gate_8 circuit breaker has quarantined this file. A
        quarantined build was pulled for repeated gate_8 failures and must
        never be promoted -- gate_8 reports it as failed, so the ingestor must
        agree or the activation governor sees a false-promote. Best-effort: if
        the breaker-state module/file isn't importable (e.g. CI), return False
        so we never invent a quarantine."""
        try:
            from gate_quality_state import is_quarantined
            return bool(is_quarantined(Path(file).name))
        except Exception:
            return False

    def evaluate(self, artifact: BuildArtifact) -> IngestVerdict:
        atype = classify(artifact.file)
        path = self._resolve(artifact)
        if self._is_quarantined(artifact.file):
            # The gate_8 circuit breaker pulled this file. Never promote a
            # quarantined build, regardless of how its current content scans --
            # gate_8 fails it, so the ingestor must too (else: false-promote
            # that blocks the governor forever).
            verdict = IngestVerdict(
                artifact=artifact, artifact_type=atype, ok=False,
                contract="quarantined",
                detail="gate_8 circuit breaker has quarantined this file",
                action=IngestAction.QUARANTINE, safety_block=False,
            )
            verdict.fix_directive = self._fix_directive(verdict)
            return verdict
        if not path.exists():
            # gate_8's `built_file_missing` check: a directive can declare an
            # output_file that never lands on disk -- investigate/diagnostic
            # directives that don't call delegate_to_builder, or a build whose
            # file was quarantined/deleted after the build_artifact row was
            # emitted. validate_file would happily pass the stored content, so
            # the ingestor would PROMOTE a phantom that gate_8 fails -> a
            # false-promote that pins the governor below its arming bar. Treat a
            # missing output file as a contract failure (quarantine), mirroring
            # gate_8 so the two graders agree.
            verdict = IngestVerdict(
                artifact=artifact, artifact_type=atype, ok=False,
                contract="built_file_missing",
                detail=f"declared output file not on disk: {path}",
                action=IngestAction.QUARANTINE, safety_block=False,
            )
            verdict.fix_directive = self._fix_directive(verdict)
            return verdict
        ok, contract, detail, safety_block = validate_file(path, atype)
        action = IngestAction.PROMOTE if ok else IngestAction.QUARANTINE
        verdict = IngestVerdict(
            artifact=artifact, artifact_type=atype, ok=ok,
            contract=contract, detail=detail, action=action,
            safety_block=safety_block,
        )
        if not ok:
            verdict.fix_directive = self._fix_directive(verdict)
        return verdict

    # ---- actions (only taken when enabled) ---------------------------------

    def _record_verdict(self, verdict: IngestVerdict) -> None:
        self.store.write("mesh_memory", {
            "agent_id": INGESTOR_AGENT_ID,
            "memory_type": "artifact_verdict",
            "content": json.dumps(verdict.to_record()),
            "importance": 0.4,
        })

    def _promote(self, verdict: IngestVerdict) -> None:
        self.store.write("mesh_memory", {
            "agent_id": INGESTOR_AGENT_ID,
            "memory_type": "artifact_promoted",
            "content": json.dumps(verdict.to_record()),
            "importance": 0.5,
        })

    def _quarantine(self, verdict: IngestVerdict) -> None:
        self.store.write("mesh_memory", {
            "agent_id": INGESTOR_AGENT_ID,
            "memory_type": "artifact_quarantined",
            "content": json.dumps(verdict.to_record()),
            "importance": 0.6,
        })
        # NO reverse-feed into the build queue. We used to write a `fix_<file>`
        # build_directive here for goose_runner to rebuild -- but that directive
        # is spec-less by construction (it carries the contract failure, not WHAT
        # the file should do), so goose can't build it: it ghosts -> the file
        # re-quarantines -> we reverse-feed again. That flood also pinned the
        # directive generator's queue gate (MIN_QUEUE_TO_SKIP), gagging the rich
        # architect (the 2026-06-03 drift). Quarantines are the BREAKER's job
        # (retire/accept/investigate via reset_breaker.py); a genuine rebuild --
        # WITH a real spec -- is the directive generator's job (it reads the
        # gaps_map + failure history). verdict.fix_directive is still populated on
        # the verdict (and recorded in artifact_quarantined above) for diagnostics.

    def _heartbeat(self, processed: int) -> None:
        self.store.write("service_health", {
            "service": "artifact_ingestor",
            "last_heartbeat": _now_iso(),
            "status": "ok",
            "detail": f"processed={processed}",
        })

    # ---- the cycle ---------------------------------------------------------

    def run_once(self) -> list[IngestVerdict]:
        """Process the next batch of new artifacts.

        Dormant: returns verdicts with action_taken=False, writes NOTHING.
        Enabled: records verdicts, promotes/quarantines, reverse-feeds fixes,
                 advances the watermark, heartbeats.
        """
        enabled = self.is_enabled()
        watermark = self.store.get_watermark()
        rows = self.store.read_build_artifacts(watermark, self.batch_limit)

        verdicts: list[IngestVerdict] = []
        seen: set[str] = set()
        max_built_at = watermark or ""

        for row_id, content in rows:
            artifact = BuildArtifact.from_mesh_content(content, row_id=row_id)
            if artifact is None:
                continue
            if artifact.dedup_key in seen:
                continue
            seen.add(artifact.dedup_key)

            verdict = self.evaluate(artifact)
            if artifact.built_at > max_built_at:
                max_built_at = artifact.built_at

            if enabled:
                self._record_verdict(verdict)
                if verdict.action is IngestAction.PROMOTE:
                    self._promote(verdict)
                else:
                    self._quarantine(verdict)
                verdict.action_taken = True
            verdicts.append(verdict)

        if enabled and verdicts:
            if max_built_at:
                self.store.set_watermark(max_built_at)
            self._heartbeat(len(verdicts))

        return verdicts

    def run_forever(self, interval_sec: int = 300, log=print) -> None:  # pragma: no cover
        """Daemon loop. Stays dormant (no writes) until activated; the process
        can run safely either way."""
        log(f"[artifact_ingestor] starting; {self.activation_reason()}")
        while True:
            try:
                verdicts = self.run_once()
                if verdicts:
                    promoted = sum(1 for v in verdicts if v.ok)
                    quarantined = len(verdicts) - promoted
                    mode = "ACTED" if self.is_enabled() else "DRY-RUN"
                    log(f"[artifact_ingestor] {mode}: {len(verdicts)} artifacts "
                        f"({promoted} promote / {quarantined} quarantine)")
            except Exception as e:  # never let the loop die
                log(f"[artifact_ingestor] cycle error: {type(e).__name__}: {e}")
            time.sleep(interval_sec)

    def status(self) -> dict:
        return {
            "enabled": self.is_enabled(),
            "activation": self.activation_reason(),
            "sentinel_home": str(self.home),
            "watermark": self.store.get_watermark(),
        }
