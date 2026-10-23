# V5 — Full MCP Lifecycle Scoring (design + build plan)

**Goal (chairman, 2026-10-18):** by **mid-November**, a smooth *integrated* pipeline where an MCP is
scored across its **whole lifecycle — born · changed · died · archived** — not just re-scored. Funded:
**~$119 API credits until Nov 5** to design/build/implement (commit + merge). Chain-bound via an sctask.

## Governing constraints (chairman directives — honour verbatim)
1. **Build on what we have.** v3.4 rescore completion is the FOUNDATION; reuse the hardened
   `weekly_rescore` harness, the registry, the scorer, the chain infra. Do NOT greenfield.
   *Finishing v3.4 unlocks the harvesting / new-data-flow work.*
2. **Fail gracefully, tower-side.** The chain is LONG. Every step durable + idempotent + resumable
   (the discipline this rescore forged: results to durable GH branch before teardown; ON CONFLICT
   upsert; resume-from-checkpoint). Must be ADDITIVE-not-essential (zocomputer runs out of AI creds and
   dies — the pipeline/spine must survive any subsystem down). No silent caps; log what's dropped.
3. **Scheduled away from other tasks (memory conflicts).** Heavy steps (GPU scoring, bulk imports)
   must be SERIALIZED and scheduled to avoid tower RAM/GPU contention with other sctasks/chains. The
   chain yields to / does not collide with other tower jobs — a scheduling guard, not best-effort.
4. **Distinct routes per lifecycle stage** (not one path): born ≠ changed ≠ died ≠ archived.
5. **"Died" is INTEL, not deletion.** A vanished MCP (taken down? why?) is tracked + retained for
   intel, never silently dropped. Disposal is an official, auditable route.

## The four lifecycle routes
| Stage | Trigger | Route | Reuses |
|---|---|---|---|
| **BORN** | daily harvester adds a registry row | first-score on ingest; enrich thin stubs so they pass the scorable filter (desc>20) instead of silently stranding | harvester, score_service, registry |
| **CHANGED** | schedule / detected change (desc, url, cert/CA, verdict) | delta-rescore under current model | hardened `weekly_rescore` (this work) |
| **DIED** | absence: not seen in harvest for N cycles | mark `dead`, retain LAST score + metadata + reason, raise intel signal | registry lifecycle_state + a disposal step |
| **ARCHIVED** | dead for M cycles | move to cold/archive store; queryable for intel/trends; excluded from live scoring | new archive table/partition |

**Integrated spine:** harvester → registry(`lifecycle_state`) → scorer(v5) → verdict → {change-detect→CHANGED;
absence-detect→DIED→ARCHIVED}. A `lifecycle_state` column (born/active/changed/dead/archived) + logged
transitions is the backbone.

## The self-aware seam-audit (institutionalises "no holdovers")
A daily DETERMINISTIC check — not a principle in a brief — that computes and ALARMS on seam drift:
`harvested-but-unscored`, `scorable-but-dropped`, `scored-but-stale`, `seen-recently-vs-marked-dead`,
`scored-under-old-model-not-current`. Turns every invisible gap LOUD so initiatives can't half-finish.
This is the direct fix for "why do things get partly built and left" (self-report ≠ end-state; seams are
nobody's job → make them a monitored, enforced number).

## v5 model
Lifecycle-aware scorer (successor to v3.4). Features may include change/age/cert signals. **Serving cutover
stays empirically-gated** (better-than-incumbent on held-out eval + corpus sanity) — the build is
chairman-authorised; the cutover is decided by the falsifiable gate, not eyeball.

## Milestones (today ~10/18 → mid-Nov)
- **W1 (~10/25):** v3.4 finalized (s5 re-fire) · `lifecycle_state` schema PR · BORN route (harvester→score
  seam closed) · seam-audit monitor v1 live.
- **W2 (~11/1):** DIED route (absence-detect + dead-mark + intel metadata/reason) · ARCHIVED route (cold store).
- **W3 (~11/8):** CHANGED route (change-detect triggers, incl. cert/CA) hardened · v5 model train/eval.
- **W4 (~mid-Nov):** integrated end-to-end on the spine · v5 empirical cutover · chain self-driving + seam-audit green.

## Chain-bind (the sctask)
A long spooled chain (ZoChainWorker-style) that advances ~one milestone-slice per run: reconcile-first →
do the bounded slice → PR + two-pole + merge-on-evidence → ledger receipt → re-arm. **Scheduled serialized,
off-peak vs other tower jobs** (resource guard); each step fails gracefully + resumable; funded by API
credits through 11/5, Max-sub thereafter for the integration tail. Reconciles against the seam-audit each run.

## Budget
~$119 API credits → the credit-heavy BUILD (BORN/DIED/ARCHIVED routes + v5 train) lands before 11/5;
the mid-Nov integration/cutover tail runs on Max sub. GPU scoring on vast (separate vast credit).

## Governance
Chairman directive = the grant for this build (prod-schema `lifecycle_state`, new model). Still-HELD within
it: the v5 **serving cutover** (empirical gate) and any **irreversible schema migration** (pre-check first).
v3.0 + the verdict-read path stay untouched until an empirically-cleared cutover.
