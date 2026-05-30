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
| `__main__.py` | CLI: `status` / `run-once` / `run`. |

## Relationship to the CI smoke ladder

This is the **host-side, mesh_memory-driven** ingestion point; the `tests/ci`
smoke ladder + `pr-gates.yml` is the **GitHub/PR-boundary** one. Same contract
philosophy (import-smoke, html, safety scan), two ingestion points — host
(24 h `build_artifact` cohorts) and PR (per-change git diff).

## Dormancy — safe by default

`run_once()` is a **read-only dry-run** until explicitly activated; it writes no
promotions, quarantines, or directives while dormant. Activate with ANY of:
`ArtifactIngestor(enabled=True)`, `ARTIFACT_INGESTOR_ENABLED=1`, or a
`.ingestor_enabled` sentinel in `$ZO_SENTINEL_HOME`. This honours the standing
"new daemon stays dormant until Robin says otherwise" rule.

## CLI

```bash
python -m zo_sentinel.ingestor status                 # enabled? watermark? home?
python -m zo_sentinel.ingestor run-once               # one cycle (dry-run if dormant)
python -m zo_sentinel.ingestor run-once --enable      # act for this one invocation
python -m zo_sentinel.ingestor run --interval 300     # daemon loop (stays dormant until a latch opens)
```

Tested hermetically in `tests/test_artifact_ingestor.py`; import-gated by the CI
smoke ladder (`tests/ci/hermetic_manifest.py`).
