# v5 full MCP lifecycle scoring — mirror of record

This directory is the **off-tower mirror of record** for the **v5 lifecycle scoring program**
(chairman mandate 2026-10-18: by mid-November, a smooth *integrated* pipeline that scores an MCP
across its whole lifecycle — **born · changed · died · archived** — via distinct routes, built on the
hardened `weekly_rescore` harness that finalized v3.4).

These files were durability-tracked here because the **live runtime copies live only in the Tower's
gitignored operating root** (`_chains/v5_lifecycle/` and `_staging/`), which is not version-controlled
and would not survive a tower loss. This mirror exists so the spec, chain definition, and insight docs
survive independently of the Tower.

**The Tower copies remain authoritative at runtime.** This is a point-in-time snapshot
(2026-10-19), not a live-synced source. If they diverge, the Tower operating-root copy is canonical;
refresh this mirror from it. Do not wire the live chain to read from here.

## Contents

| File | Role | Source on Tower |
| --- | --- | --- |
| `CHAIN.json` | The spooled chain definition (12 segments V00–V11, invariants, two-pole verify gates). | `_chains/v5_lifecycle/CHAIN.json` |
| `artifacts/v5_baseline.json` | V00 baseline: finalized v3.4 state (334,754 scored) the lifecycle routes compare against. | `_chains/v5_lifecycle/artifacts/v5_baseline.json` |
| `V5_LIFECYCLE_SCORING.md` | The **design + build plan** (four lifecycle routes, seam-audit, milestones, budget, governance). | `_staging/V5_LIFECYCLE_SCORING.md` (design-doc revision) |
| `V34_INSIGHTS_2026-10-19.md` | Insight doc: what changed in v3.4 vs v3.0 across the ~334k corpus. | `_staging/V34_INSIGHTS_2026-10-19.md` |
| `MCP_LANDSCAPE_FACTS_2026-10-19.md` | Insight doc: ecosystem facts from the 334,754 scored servers. | `_staging/MCP_LANDSCAPE_FACTS_2026-10-19.md` |

## Provenance notes (read before refreshing)

- **`V5_LIFECYCLE_SCORING.md`** — `CHAIN.json`'s `view_path` points at
  `_staging/V5_LIFECYCLE_SCORING.md`, and the chain driver **regenerates that path as an
  auto-status page on every run** ("do not edit it"). The file mirrored here is the original
  **design/build-plan** document (the spec the task asked to preserve), not the regenerated status
  page. When refreshing, take the design-doc revision, not the live status-page overwrite.
- **`MCP_LANDSCAPE_FACTS_2026-10-19.md`** — at snapshot time this file was present only in a working
  checkout's `_staging/`, not yet in the operating-root `_staging/`. Captured here so it is not lost.

No secrets are included. Files are copied byte-for-byte unmodified from the Tower.
