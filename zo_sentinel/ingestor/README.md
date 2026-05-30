# `zo_sentinel.ingestor` — net-new code-artifact ingestor

The single coherent ingestor for the artifacts the app generates about itself.
zo-sentinel builds itself: `directive_factory`/`goose_runner` dispatch build
directives, the `t1.zo_sentinel_builder` tier generates new modules/views/docs,
and each generated file is registered as a `build_artifact` row in
`mesh_memory`. This package subscribes to those rows and closes the loop.

```
build directive ──> goose_runner / t1.zo_sentinel_builder ──> generated file
                                                                     │
                                          registers build_artifact row in mesh_memory
                                                                     │
                                                  ┌──────────────────▼──────────────────┐
                                                  │          ArtifactIngestor            │
                                                  │  classify → static safety scan →     │
                                                  │  type contract (import / html / …)   │
                                                  └───────┬───────────────────┬──────────┘
                                                   PASS   │                   │  FAIL
                                                  promote │                   │ quarantine
                                                          ▼                   ▼
                                              artifact_promoted     artifact_quarantined
                                                                              +
                                                                build_directive (fix) ──> goose_runner
```

## Pieces

| Module | Role |
|---|---|
| `model.py` | `BuildArtifact` (mirrors the mesh_memory row), `ArtifactType`, `IngestVerdict`. |
| `contracts.py` | Per-type validation, **faithfully modelled on `tests/gates/gate_8_new_module.py`**: static safety scan (DROP/DELETE/TRUNCATE on protected core tables = auto-fail, *before* any import), enrichment `compute_score` contract, HTML interactivity, isolated `.py` import. |
| `store.py` | The mesh_memory seam. `InMemoryMeshStore` (hermetic tests) / `HttpMeshStore` (write_service at `$ZO_WRITE_SERVICE`). |
| `ingestor.py` | `ArtifactIngestor` — poll → dedup (by file+built_at, watermark) → validate → promote / quarantine + reverse-feed fix-directive. |
| `governor.py` | `AutoActivationGovernor` — decides *when* the ingestor has earned activation and writes the latch itself (see below). |
| `__main__.py` | CLI: `status` / `run-once` / `run` / `govern` / `govern-status`. |

## Relationship to the CI smoke ladder

This is the **host-side, mesh_memory-driven** ingestion point; the `tests/ci`
smoke ladder + `pr-gates.yml` is the **GitHub/PR-boundary** one. Same contract
philosophy (import-smoke, html, safety scan), two ingestion points — host
(24 h `build_artifact` cohorts) and PR (per-change git diff).

## Dormancy — safe by default

`run_once()` is a **read-only dry-run** until activated; it writes no promotions,
quarantines, or directives while dormant. Activation is signalled by ANY of:
`ArtifactIngestor(enabled=True)`, `ARTIFACT_INGESTOR_ENABLED=1`, or a
`.ingestor_enabled` sentinel in `$ZO_SENTINEL_HOME`.

## Auto-activation governor — how the latch gets created

Nothing in the build/gate loop creates `.ingestor_enabled`; activation is its own
evidence-gated decision made by `AutoActivationGovernor`. Each governance cycle:

1. **self-smoke** — a known-good and known-bad fixture run through the ingestor's
   own `evaluate()` must return the expected verdicts (logic intact);
2. **dry-run** the pending artifacts and compare each verdict to **gate_8's**
   verdict (the trusted host oracle, via a `Gate8VerdictSource` seam);
3. a cycle is **green** iff self-smoke passes, there are **zero false-promotes**
   (never promote what gate_8 failed), and agreement among comparable artifacts
   is ≥ `min_agreement`.

It **auto-writes the latch** once `consecutive_green ≥ N`, `distinct agreeing
artifacts ≥ K`, and lifetime false-promotes is 0. The latch it writes is
**content-bearing** — it records who/when/why (the provenance label) — so an
`.ingestor_enabled` file is no longer a bare toggle. An `audit_log` row
(`INGESTOR_AUTO_ACTIVATED`) is emitted too.

**Veto / freeze:** a `.no_auto_activate` file in `$ZO_SENTINEL_HOME` (or env
`NO_AUTO_ACTIVATE`) blocks activation *and freezes* an already-active ingestor
(the governor removes the latch). This is the human override.

**CI-safe:** with no `gate_errors.db` (the CI situation) every gate_8 verdict is
unknown → no agreeing artifacts → the governor never activates. Governor state
lives in `mesh_memory` (durable), so the latch is re-asserted after a container
restart wipes the local file.

## CLI

Run these from the repo root **on the host** (ZoComputer), where write_service is
reachable at `127.0.0.1:8772`:

```bash
# the ingestor
python -m zo_sentinel.ingestor status                 # enabled? watermark? home?
python -m zo_sentinel.ingestor run-once               # one cycle (dry-run if dormant)
python -m zo_sentinel.ingestor run-once --enable      # act for this one invocation
python -m zo_sentinel.ingestor run --interval 300     # daemon loop (dormant until a latch opens)

# the auto-activation governor
python -m zo_sentinel.ingestor govern-status          # readiness: green streak, agreeing artifacts, vetoed?
python -m zo_sentinel.ingestor govern                 # run one governance cycle (auto-writes latch when ready)
python -m zo_sentinel.ingestor govern --propose       # assess only; never write the latch
```

Tested hermetically in `tests/test_artifact_ingestor.py`; import-gated by the CI
smoke ladder (`tests/ci/hermetic_manifest.py`).
