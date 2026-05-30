"""
governor.py -- AutoActivationGovernor: automated, evidence-gated activation of
the artifact ingestor.

The ingestor ships dormant. Rather than a human `touch`ing `.ingestor_enabled`,
this governor decides when the ingestor has *earned* activation and writes the
latch itself. The readiness bar (chosen 2026-05-29):

  A cycle is GREEN iff:
    1. self-smoke passes -- a known-good and a known-bad fixture run through the
       ingestor's own evaluate() return the expected verdicts (logic intact);
    2. no false-promote -- the ingestor never PROMOTED an artifact that gate_8
       judged a failure (the safety-critical disagreement);
    3. agreement holds -- among artifacts gate_8 has ALSO judged, the ingestor's
       verdict matches gate_8's at >= min_agreement.

  ACTIVATE (write the latch) iff, and only iff:
    * not vetoed, AND
    * consecutive_green_cycles >= min_consecutive_green, AND
    * distinct agreeing artifacts seen >= min_agreeing_artifacts, AND
    * zero lifetime false-promotes.

Fully automatic with a veto: a `.no_auto_activate` file in the sentinel home (or
env NO_AUTO_ACTIVATE) blocks activation AND freezes an already-active ingestor
(the governor removes the latch). The latch it writes is content-bearing -- it
records who/when/why (provenance label) -- which is also how an `.ingestor_enabled`
file becomes "labelled".

Hermetic: gate_8 verdicts come through a Gate8VerdictSource seam (InMemory for
tests, DuckDB on the host). In CI there is no gate_errors.db, so every verdict is
unknown -> no agreeing artifacts -> the governor never activates. Safe by default.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from zo_sentinel.ingestor.ingestor import ArtifactIngestor, SENTINEL_NAME
from zo_sentinel.ingestor.model import BuildArtifact
from zo_sentinel.ingestor.store import MeshStore

VETO_NAME = ".no_auto_activate"
GOVERNOR_AGENT_ID = "zo_sentinel.activation_governor"
ACTIVATION_STATE_TYPE = "ingestor_activation_state"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# gate_8 verdict seam
# ---------------------------------------------------------------------------

class Gate8VerdictSource(Protocol):
    def verdict_for(self, file: str) -> Optional[bool]:
        """True=gate_8 passed it, False=failed, None=gate_8 hasn't judged it."""
        ...


class InMemoryGate8Source:
    """Hermetic gate_8 oracle for tests. Map file -> pass/fail."""

    def __init__(self, verdicts: Optional[dict[str, bool]] = None):
        self._v = dict(verdicts or {})

    def set(self, file: str, ok: bool) -> None:
        self._v[file] = ok

    def verdict_for(self, file: str) -> Optional[bool]:
        # match by basename so absolute/relative paths line up
        base = Path(file).name
        for k, v in self._v.items():
            if Path(k).name == base:
                return v
        return None


class DuckDBGate8Source:
    """Reads gate_8's recorded verdicts from gate_errors.db (gate_checks). A file
    is failed if any gate_8 check for it is fail/error, passed if it has only
    passes, unknown if gate_8 hasn't checked it. Any error -> unknown (safe)."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.environ.get("GATE_ERRORS_DB", "/home/workspace/gate_errors.db")

    def verdict_for(self, file: str) -> Optional[bool]:
        base = Path(file).name
        try:
            import duckdb
            con = duckdb.connect(self.db_path, read_only=True)
            try:
                rows = con.execute(
                    "SELECT status FROM gate_checks "
                    "WHERE gate_name = 'gate_8_new_module' AND check_name LIKE ?",
                    [f"%{base}%"],
                ).fetchall()
            finally:
                con.close()
        except Exception:
            return None
        if not rows:
            return None
        statuses = {r[0] for r in rows}
        if {"fail", "error"} & statuses:
            return False
        if "pass" in statuses:
            return True
        return None


# ---------------------------------------------------------------------------
# Criteria + state
# ---------------------------------------------------------------------------

@dataclass
class ActivationCriteria:
    min_consecutive_green: int = 3      # N
    min_agreeing_artifacts: int = 10    # K distinct artifacts agreeing with gate_8
    min_agreement: float = 0.95         # per-cycle agreement among comparable artifacts


@dataclass
class GovernorState:
    consecutive_green: int = 0
    agreeing_artifacts: list[str] = field(default_factory=list)  # distinct files
    lifetime_false_promotes: int = 0
    activated: bool = False
    activated_at: str = ""
    last_cycle_at: str = ""
    last_detail: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: Optional[str]) -> "GovernorState":
        if not text:
            return cls()
        try:
            d = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return cls()
        st = cls()
        st.consecutive_green = int(d.get("consecutive_green", 0))
        st.agreeing_artifacts = list(d.get("agreeing_artifacts", []))
        st.lifetime_false_promotes = int(d.get("lifetime_false_promotes", 0))
        st.activated = bool(d.get("activated", False))
        st.activated_at = str(d.get("activated_at", ""))
        st.last_cycle_at = str(d.get("last_cycle_at", ""))
        st.last_detail = str(d.get("last_detail", ""))
        return st


@dataclass
class CycleAssessment:
    green: bool
    self_smoke_ok: bool
    comparable: int          # artifacts gate_8 also judged this cycle
    agreed: int              # of those, how many matched
    false_promotes: int      # ingestor promoted, gate_8 failed
    new_agreeing: list[str]  # distinct agreeing files newly counted
    detail: str


# ---------------------------------------------------------------------------
# self-smoke fixtures
# ---------------------------------------------------------------------------

_GOOD_FIXTURE = "VALUE = 1\ndef helper():\n    return VALUE\n"
# a module-level forbidden mutation on a protected core table -> must be blocked
_BAD_FIXTURE = 'SQL = "DROP TABLE mesh_memory"\n'


# ---------------------------------------------------------------------------
# Governor
# ---------------------------------------------------------------------------

class AutoActivationGovernor:
    def __init__(self, ingestor: ArtifactIngestor, *,
                 gate8: Optional[Gate8VerdictSource] = None,
                 store: Optional[MeshStore] = None,
                 criteria: Optional[ActivationCriteria] = None,
                 auto: bool = True):
        self.ingestor = ingestor
        self.store: MeshStore = store or ingestor.store
        self.gate8: Gate8VerdictSource = gate8 or DuckDBGate8Source()
        self.criteria = criteria or ActivationCriteria()
        self.auto = auto
        self.home = ingestor.home

    # ---- veto --------------------------------------------------------------

    def is_vetoed(self) -> bool:
        if os.environ.get("NO_AUTO_ACTIVATE", "").strip() in ("1", "true", "yes"):
            return True
        return (self.home / VETO_NAME).exists()

    # ---- state -------------------------------------------------------------

    def load_state(self) -> GovernorState:
        return GovernorState.from_json(
            self.store.read_latest(ACTIVATION_STATE_TYPE, GOVERNOR_AGENT_ID))

    def save_state(self, state: GovernorState) -> None:
        state.last_cycle_at = _now_iso()
        self.store.write("mesh_memory", {
            "agent_id": GOVERNOR_AGENT_ID,
            "memory_type": ACTIVATION_STATE_TYPE,
            "content": state.to_json(),
            "importance": 0.5,
        })

    # ---- self-smoke --------------------------------------------------------

    def self_smoke(self) -> tuple[bool, str]:
        """Run a known-good and known-bad artifact through the ingestor's own
        evaluate(). Good must PROMOTE; bad must QUARANTINE with a safety block."""
        with tempfile.TemporaryDirectory() as td:
            good = Path(td) / "selfsmoke_ok.py"
            bad = Path(td) / "selfsmoke_bad.py"
            good.write_text(_GOOD_FIXTURE, encoding="utf-8")
            bad.write_text(_BAD_FIXTURE, encoding="utf-8")
            vg = self.ingestor.evaluate(BuildArtifact(file=str(good)))
            vb = self.ingestor.evaluate(BuildArtifact(file=str(bad)))
        if not vg.ok:
            return False, f"self-smoke: known-good was rejected ({vg.contract}: {vg.detail})"
        if vb.ok or not vb.safety_block:
            return False, "self-smoke: known-bad was not safety-blocked"
        return True, "self-smoke ok"

    # ---- per-cycle assessment ---------------------------------------------

    def assess_cycle(self, state: GovernorState) -> CycleAssessment:
        smoke_ok, smoke_detail = self.self_smoke()

        # Pure dry-run: read pending artifacts and evaluate() each. We do NOT
        # call ingestor.run_once() here -- this must never take an action,
        # regardless of the ingestor's enabled state, and must not move the
        # watermark (so artifacts remain available until real activation).
        watermark = self.store.get_watermark()
        rows = self.store.read_build_artifacts(watermark, self.ingestor.batch_limit)
        verdicts = []
        seen: set[str] = set()
        for row_id, content in rows:
            art = BuildArtifact.from_mesh_content(content, row_id=row_id)
            if art is None or art.dedup_key in seen:
                continue
            seen.add(art.dedup_key)
            verdicts.append(self.ingestor.evaluate(art))

        comparable = agreed = false_promotes = 0
        new_agreeing: list[str] = []
        already = set(state.agreeing_artifacts)
        for v in verdicts:
            g = self.gate8.verdict_for(v.artifact.file)
            if g is None:
                continue
            comparable += 1
            if v.ok == g:
                agreed += 1
                key = v.artifact.dedup_key
                if key not in already:
                    already.add(key)
                    new_agreeing.append(key)
            elif v.ok and not g:
                # ingestor would PROMOTE something gate_8 failed -> dangerous
                false_promotes += 1

        agreement = (agreed / comparable) if comparable else 1.0
        green = (
            smoke_ok
            and false_promotes == 0
            and (comparable == 0 or agreement >= self.criteria.min_agreement)
        )
        detail = (f"{smoke_detail}; comparable={comparable} agreed={agreed} "
                  f"agreement={agreement:.2f} false_promotes={false_promotes}")
        return CycleAssessment(green, smoke_ok, comparable, agreed,
                               false_promotes, new_agreeing, detail)

    # ---- latch I/O ---------------------------------------------------------

    def _write_latch(self, state: GovernorState, evidence: dict) -> None:
        label = {
            "enabled_by": GOVERNOR_AGENT_ID,
            "enabled_at": state.activated_at,
            "mode": "auto",
            "scope": "all_artifact_types",
            "criteria": asdict(self.criteria),
            "evidence": evidence,
        }
        (self.home / SENTINEL_NAME).write_text(json.dumps(label, indent=2), encoding="utf-8")

    def _remove_latch(self) -> bool:
        latch = self.home / SENTINEL_NAME
        if latch.exists():
            latch.unlink()
            return True
        return False

    def _audit(self, event_type: str, action: str, details: dict) -> None:
        self.store.write("audit_log", {
            "event_type": event_type,
            "actor": GOVERNOR_AGENT_ID,
            "target_server_id": "artifact_ingestor",
            "action": action,
            "outcome": "ok",
            "details_json": json.dumps(details),
            "immutable": True,
            "timestamp": _now_iso(),
        })

    # ---- the cycle ---------------------------------------------------------

    def run_once(self) -> dict:
        """One governance cycle: assess readiness, then activate / freeze /
        hold. Returns a status dict (also safe to call repeatedly)."""
        state = self.load_state()
        self.home.mkdir(parents=True, exist_ok=True)

        # Veto wins over everything: freeze if currently active.
        if self.is_vetoed():
            froze = False
            if state.activated:
                self._remove_latch()
                state.activated = False
                froze = True
                self._audit("INGESTOR_FROZEN", "disable", {"reason": "veto present"})
            state.last_detail = "vetoed"
            self.save_state(state)
            return {"action": "vetoed", "froze": froze, "activated": False,
                    "consecutive_green": state.consecutive_green}

        assessment = self.assess_cycle(state)

        # update counters
        if assessment.green:
            state.consecutive_green += 1
        else:
            state.consecutive_green = 0
        state.agreeing_artifacts.extend(assessment.new_agreeing)
        state.lifetime_false_promotes += assessment.false_promotes
        state.last_detail = assessment.detail

        ready = (
            state.consecutive_green >= self.criteria.min_consecutive_green
            and len(state.agreeing_artifacts) >= self.criteria.min_agreeing_artifacts
            and state.lifetime_false_promotes == 0
        )

        action = "hold"
        if state.activated:
            # already on: re-assert the latch in case a restart wiped the file
            action = "reasserted" if not (self.home / SENTINEL_NAME).exists() else "active"
            if action == "reasserted":
                self._write_latch(state, {"reasserted_at": _now_iso()})
        elif ready and self.auto:
            state.activated = True
            state.activated_at = _now_iso()
            evidence = {
                "consecutive_green": state.consecutive_green,
                "agreeing_artifacts": len(state.agreeing_artifacts),
                "lifetime_false_promotes": state.lifetime_false_promotes,
            }
            self._write_latch(state, evidence)
            self._audit("INGESTOR_AUTO_ACTIVATED", "enable", evidence)
            action = "activated"
        elif ready and not self.auto:
            # propose-only mode: record readiness, leave the flip to an operator
            self._audit("INGESTOR_ACTIVATION_READY", "propose", {
                "consecutive_green": state.consecutive_green,
                "agreeing_artifacts": len(state.agreeing_artifacts),
            })
            action = "proposed"

        self.save_state(state)
        return {
            "action": action,
            "green": assessment.green,
            "consecutive_green": state.consecutive_green,
            "agreeing_artifacts": len(state.agreeing_artifacts),
            "lifetime_false_promotes": state.lifetime_false_promotes,
            "activated": state.activated,
            "ready": ready,
            "detail": assessment.detail,
        }

    def status(self) -> dict:
        state = self.load_state()
        return {
            "auto": self.auto,
            "vetoed": self.is_vetoed(),
            "activated": state.activated,
            "consecutive_green": state.consecutive_green,
            "agreeing_artifacts": len(state.agreeing_artifacts),
            "needs": {
                "consecutive_green": self.criteria.min_consecutive_green,
                "agreeing_artifacts": self.criteria.min_agreeing_artifacts,
            },
            "lifetime_false_promotes": state.lifetime_false_promotes,
            "last_cycle_at": state.last_cycle_at,
        }
