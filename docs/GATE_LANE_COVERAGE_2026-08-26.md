# Which lane does each gate sit on, and what fraction of builds passes through it?

**Measured** 2026-08-26 · window **2026-08-19 → 2026-08-26** (7 days) · repo `rob531/zo-sentinel`

The standing rule this applies is in `KNOWLEDGE_BASE.md` §CONTROL PLACEMENT RULE.
**Findings only. Nothing here is fixed by this document.**

---

## 1. What the lanes actually carry

| Lane | Volume in the window | Share |
|---|---|---|
| **Engine path** (`autonomous-build` PRs) | **490 PRs** | **94.8%** |
| — `ladder:zo-ladder-low` | 348 | 67.3% |
| — `ladder:zo-ladder-medium` | 102 | 19.7% |
| Human / other PRs | 27 | 5.2% |
| **goose-canary lane** | **0 runs** | **0%** |
| **Total PRs created** | 517 | 100% |

Supporting workflow-run counts over the same window: `evaluator` 1000+, `bandit` 572, `pr-gates` 540, `integration` 540, `referent-verify` 44, `ui-smoke` 21, `e2e-nightly` 8, `ui-bakeoff` 0, `goose-canary` 0.

**The engine path is the volume. It is not close.** Anything not on it is gating ~5% of the repo's output at best.

---

## 2. Gate → lane → coverage

| Gate | Sits on | Fraction of builds it sees | Note |
|---|---|---|---|
| `capmap-check` | every PR | **100%** | required |
| `static-analysis` (ruff `F,E9`) | every PR | **100%** of PRs; file scope `zo_sentinel tests/ci tests/gates app tools` | widened to `app/`+`tools/` by #3992 on 2026-08-26. **`services/**` — where the engine emits — is still report-only.** |
| `smoke-ladder` | every PR | **100%** | tiers 0–4, whole-tree |
| `no-hollow` | every PR | **100%** | required |
| `pytest` | every PR | **100%** | required |
| `frontend` | every PR | **100%** | required |
| reachability ratchet (in `pr-gates`) | every PR | **100%** of PRs, but the census scans **root-level `.py` only** | the 930+ staged services are invisible to it — audit finding G4, still open |
| **`schema-prm` (CI)** | every PR | **~2% of engine emissions** | **see §3** |
| schema PRM at emission (`goose_runner._schema_prm_gate`) | **engine path, at emission** | **100% of goose_runner builds** | the correctly-placed one. Now also carries the `:8772` SQL-string check (#4068). |
| `referent-verify` | daily schedule **+** PRs touching `app/**` or `services/**` | **on the engine path** — 42 `pull_request` runs since 2026-08-26 | routes armed (#4067); tables/columns report-only |
| `pr-triage` | `autonomous-build` PRs only | 94.8% of PRs | labels only; merges nothing |
| `auto-merge` | `autonomous-build` + `triage:solid` | 94.8% of PRs | **0% of human-authored PRs** — issue #4032 Phase A |
| `goose-canary` | canary lane | **0%** | **dark since 2026-08-10** — see §4 |
| `ui-bakeoff` | — | **0%** | 0 runs in the window |

---

## 3. The live repeat of §2b: `schema-prm` inspects ~2% of what the engine writes

`schema-prm` is a **required, blocking** check. It runs on **100%** of PRs — 540 in the window. That number is what makes it look covered, and it is the wrong number.

`tests/ci/schema_prm_check.py:31`:

```python
if "/" in f:                      # root-level single-file modules only
    continue
```

Every engine emission lands under `services/staged/**`:

```
#4066  services/staged/server_risk_tier_transition_check/service.toml
#4064  services/staged/axis_velocity_trend_scoring_consumer/__init__.py
#4063  services/staged/service_unit_promotion_readiness_report/contract.py
```

**Measured: of 60 consecutive merged `autonomous-build` PRs, 1 contained a root-level `.py`.**

So the repo's dedicated schema gate examines roughly **2%** of engine-path output. This is the identical shape to the June fix on the canary lane — a correct control attached to a path that does not carry the volume — and it is live today, in the gate specifically built to be the schema backstop.

The emission-time gate in `goose_runner` **is** correctly placed and does cover 100% of goose_runner builds. That is why closing the SQL-string blind spot there (#4068) reaches the engine path and closing it only in CI would not have.

---

## 4. A lane with zero runs is a control with zero coverage

`goose-canary` has not run since **2026-08-10**, and its last three runs were `failure`, `failure`, `cancelled`. Sixteen days dark, with no alarm.

This matters because it is the lane the audit already identified as the wrong home for the June schema fix. That fix is no longer merely *under-covering* — its lane executes **nothing at all**. `ui-bakeoff` is likewise at 0 runs.

Neither is diagnosed here. Both are recorded so the next session inherits a measurement rather than an assumption.

---

## 5. What the two numbers say

- Six required checks genuinely sit on 100% of builds: `capmap-check`, `static-analysis`, `smoke-ladder`, `no-hollow`, `pytest`, `frontend`.
- Three controls have a **file-scope** far narrower than their **trigger scope**: `schema-prm` (~2% of engine output), the reachability census (root-level only), and `static-analysis` (`services/**` report-only).
- Two lanes are dark: `goose-canary`, `ui-bakeoff`.
- One control covers 0% of the population it would most help: `auto-merge` on human-authored PRs.

**Trigger scope is not coverage.** Every one of these gates runs. What differs is what its own filter admits once it does.
