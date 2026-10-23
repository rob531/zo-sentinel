# Adoption sketch — `ruvnet/autogenous` + `ruvnet/ruClip` mapped onto our authority envelope

- **Date:** 2026-10-05
- **Author:** zo-sentinel design session (Brief 1, task-model redesign)
- **Status:** DESIGN ONLY — no code changed, **no authority clause widened** (clause changes are chairman-only). This document ends at a reviewed design + one Gap Register row (GR-25). It proposes; it does not adopt.
- **Register row:** `LOCO_CHAIRMAN.md` GR-25
- **Grounded in (ours):** `authority.json`, `_tools/authority.py` (`may()`), `_tools/peer_review.py` (`--propose/--falsify/--act/--sweep`), `FOLLOWUPS.md`, `explode_followups_to_memory.py`
- **Grounded in (theirs — implementation, not READMEs; cloned & read 2026-10-05):**
  `autogenous/crates/agl-types/src/lib.rs`, `crates/promotion/src/lib.rs`, `crates/constitution/src/lib.rs`, `crates/envelope/src/lib.rs`;
  `ruClip/src/control-plane/approval/transition-approval-state.ts`, `heartbeat/fire-heartbeat.ts`, `governance/propose-budget-mutation.ts`, `governance/autogenous-client.ts`, `authorization/claims-authorization.ts`, `schema/{witness,company,goal,issue}.ts`

> **Success test for this doc:** a chairman reads the one table in §2 and decides which increments (§4) to authorize. Nothing here acts.

---

## 1. What each outsider actually is (parsed from source)

### autogenous (Rust, MIT) — a *typed* governed-evolution control plane

A mutation is **not a text patch**; it is a typed transform between valid genomes
(`agl-types/src/lib.rs:1-20`). Admission is a total, clock-free structural check,
`Mutation::admissible()` (`agl-types/src/lib.rs:220-264`), returning the first of
seven `AdmissionError` variants:

1. **Authority never expands** — `requested_authority > parent.capability_ceiling → AuthorityExpansion`. `Authority` is a strict ordered lattice `ObserveOnly < SimulateOnly < AutoReversible < Governed < Constitutional` (`:26-37`).
2. **Scope floor** — each `MutationScope` (10, ordered by risk) has a `min_authority()` (`:70-84`); code/schema/security can't ride `AutoReversible`.
3. **Invariant preservation** — every parent-true `HardInvariant` must remain true (`:242-252`).
4. **Rollback is structural** — `rollback_target: None → NoRollback` (`:254-256`).
5. **Expiry is ENFORCING** — `now >= expires_at → Expired` (`:258-262`).
6. **Constitutional scope is never auto-admissible** (`:224-226`).
7. Parent-hash must match (`:221-223`).

Promotion is a **hard AND-gate over a fitness vector**, `FitnessVector::passes_hard_gates()`
(`:162-169`): `safety`, `governance`, `false_positive_rate`, `p99_overhead_ms`,
`regression_count == 0`, **and** `rollback_verified` — min-semantics, no weighted
sum can offset a safety or governance miss.

The cryptographic promotion path lives in `envelope/src/lib.rs` + `promotion/src/lib.rs`:
- `EvaluationReceipt` is a **judge's Ed25519-signed** measurement binding candidate *and* parent on the same corpus with a Wilson-95% CI (`envelope:129-207`).
- `verify_promotion()` requires **≥2 distinct *pinned* judges** (`:517-525`), takes the **worst case across every judge** (one failing judge blocks, `:532-557`), checks beats-parent with a non-inferiority margin, gates on measured numbers, rejects prohibited effects, requires a resolvable **non-self** rollback target, and requires an independently **re-derived** proof artifact per invariant (`:572-599`). It returns *every* violation, not the first.
- `PromotionEnvelope` is the controller's Ed25519-signed decision binding constitution-hash + candidate-hash + receipt-hashes + **nonce + expiry** (`:209-262`).
- `VerifiedPromotion` is **single-use** (nonce consumed), **binding** (candidate + rollback must match), and **expiring** (`promotion:160-207`); the canary auto-rolls-back on the first hard-gate violation (`promotion:99-110`).
- The **constitution** is content-hash pinned and externally governed (`constitution:1-13`); a change needs **≥2 distinct known signers + a migration path** and is representable-but-never-applicable-by-the-runtime (`constitution:105-135`).

### ruClip (TypeScript) — a company-of-agents control plane that *composes* autogenous

- **Approval-gate state machine** `transitionApprovalState()` (`approval/transition-approval-state.ts:57-123`): exactly **four legal `(action, fromState)→toState` rows** in a declarative table (`:21-26`) — `submit draft→pending`, `approve pending→approved`, `reject pending→rejected`, `revise rejected→draft`; illegal transition throws; **reject requires a reason**; actor must be `active`; **self-approval is banned** (`actor.id !== previousTransition.actorId`, `:95-99`). `isLegalApprovalTransition()` (`:35-41`) is re-run by the store as **Guard A** defence-in-depth against a *forged* transition object.
- **Append-only witness** (`schema/witness.ts`) is only a **consumer seam** — "no client exists yet" (`:1-5`). Work-ownership is real, reusing ruflo's `claims_*` MCP tools (`authorization/claims-authorization.ts`): company-scoped claim ids, live-claim verification as defence-in-depth.
- **Memory schema** `Company → Goal → Issue`: `Company.budget{total,spent,hardStopThreshold}` (`schema/company.ts`), `Goal.successCriteria[]` = "each entry one independently checkable success condition" + `budgetAllocation` carve-out (`schema/goal.ts`), `Issue.budgetImpact` **frozen once it leaves draft** (Guard B) and `approvalTransitionRef` (checked, not the mutable state field) (`schema/issue.ts`).
- **Budget-gated heartbeat** `fireHeartbeat()` (`heartbeat/fire-heartbeat.ts:65-126`): **two gates before the wake publishes** — application budget, then an operating-spend circuit breaker (`checkOperatingBudget → HARD_STOP`); either blocking **PAUSES the durable schedule (fail closed)** rather than silently skipping; comms are best-effort and never block the domain op.
- **Composition**: `propose-budget-mutation.ts` is the concrete wiring — an **edge-triggered** (not level-triggered, `:88-123`) WARNING streak builds a `routing_budget`-scoped, `auto_reversible` `Mutation` with a **required rollback_target**, posts it to autogenous `/v1/agl/admit` over OIDC (`autogenous-client.ts`), canaries on admit, and **persists even REJECTED proposals** as audit. autogenous runs as a remote Cloud Run service; ruClip holds the *client*, not the engine.

---

## 2. The mapping table — STEAL / HAVE / SKIP

> Read this column: **STEAL** = more rigorous than ours, worth a bounded increment. **HAVE** = we already do this, equally or *more* strictly. **SKIP** = deliberately not taken (philosophy mismatch, wrong domain, or TOS/infra cost).

| # | Their component (file:symbol) | Our equivalent (clause / tool) | Verdict | One-line why |
|---|---|---|---|---|
| 1 | autogenous AGL **typed mutation**: scope+authority+invariants+evidence+expiry+rollback (`agl-types:Mutation`) | `peer_review.py` proposal (clause/action/evidence/revert/verify) + `expiry_contract` | **STEAL (partial)** | We carry evidence/revert/verify already; we lack a *typed scope+authority-class tag* and an *enforcing expiry on proposals*. |
| 2 | **Authority never expands** — machine-checked ordered lattice (`agl-types:admissible` Rule 1) | "authority never silently expands" (chairman-only widening) + `re_entry_rule` stickiness | **HAVE (principle) / SKIP (lattice)** | The principle is ours and `expiry_contract.source` already credits autogenous; a full genome-lineage lattice needs a parent-genome model we don't have. |
| 3 | **Scope floor** — class carries a minimum rigor (`agl-types:min_authority`) | delegated/escalate split; `prod_deploy_class_b` + `migration_content_class` GREEN gate | **HAVE** | A class already pins its minimum rigor (Class B needs a GREEN content-class before peer clearance). |
| 4 | **Rollback REQUIRED at admission** (`agl-types:NoRollback`; `envelope` resolvable non-self) | `peer_review --propose` refuses no `--revert`; `--revert-check` must prove rc∈{0,4} | **HAVE — and we are STRICTER** | autogenous checks the field is present/resolvable; we **execute** the revert probe before the act. |
| 5 | **Min-semantics hard AND-gate** (`agl-types:passes_hard_gates`) | 8/8 fire preconditions (ALL) + `spend_ok` (3 ceilings, ALL) + `emergency.may_NEVER_overrule` | **HAVE** | Our gates are already hard ANDs; there is no weighted-score path for safety to be offset. |
| 6 | **Ed25519 ≥2 distinct pinned judges + signed envelope** (`envelope:verify_promotion`) | `peer_review` adversary≠filer + **executed** falsification + positive control + proven revert | **SKIP (quorum) / STEAL (identity, partial)** | We *deliberately chose adversary over quorum* (agreement is our dominant failure); but our identity is a taskId **string**, not a key — sign the decision tuple to close CHAOS-C7. |
| 7 | **Single-use binding promotion artifact** (nonce/candidate/expiry) (`promotion:promote`) | `peer_review` idempotency + ACTED/COMPLETE terminus + `--sweep` | **HAVE (mostly)** | Idempotent transitions + atomic content-hash writes; a nonce-replay guard only matters if signing (#6) is adopted. |
| 8 | **Immutable hash-pinned constitution** (`constitution:hash`, verifier refuses mismatch) | `authority.json` + `_followup_backups` revert + `retired_prose_live()` drift scan | **STEAL (hash-pin, report-only) / SKIP (2-of-N signers)** | Nothing today cryptographically detects an out-of-band edit to the envelope; a single chairman apex can't supply a 2-signer quorum. |
| 9 | **Append-only ledger; persist rejected too** (`autogenous ledger`; ruClip audit) | `FOLLOWUPS.md` (append-only emitters) + `peer_log.jsonl` (`--parity/--regen`) | **HAVE** | `peer_log` already makes `peer_decisions.json` regenerable from an append-only log; FALSIFIED rows are kept. |
| 10 | **Approval state machine** — declarative legal-transition table + Guard-A recompute (`transitionApprovalState`) | `may()` clause dispatch (a classifier) + `peer_review` PROPOSED→CLEARED/FALSIFIED→ACTED→COMPLETE (imperative guards) | **HAVE (self-approval=adversary≠filer) / STEAL (small clarity)** | Our transition guards are proven (16/16 self-test, chaos C1–C8) but scattered; a declarative table + recompute-legality guard would make `peer_review` as auditable as ruClip's. |
| 11 | **Claims/witness append-only record** (`claims-authorization.ts`; `witness.ts`) | `peer_decisions.json` + `peer_log.jsonl` + `FOLLOWUPS.md` | **HAVE** | ruClip's witness is only a seam; our concrete records are ahead of it. |
| 12 | **Company/Goals/Issues schema** (`schema/{company,goal,issue}.ts`) | FU records + Gap Register + FU `verify:` predicate | **HAVE (verify:=successCriteria) / SKIP (multi-tenant hierarchy)** | `Goal.successCriteria[]`≈ our `verify:`; the company/goal/issue tree is a different (multi-tenant SaaS) domain. |
| 13 | **Budget-gated heartbeat** — 2 gates, fail-closed **PAUSE**, re-checked each wake, edge-triggered (`fireHeartbeat` + `propose-budget-mutation`) | `spend_ok` + `rate_ok`/`max_fires_per_24h` + `creds_runway.py` (GR-21) | **STEAL (design)** | Partial cure for the known gap — "a ceiling only as alive as its watcher is not a ceiling" (`above_the_ceilings_why_still_held_2026_09_01`, FU-342). Re-checking on the *next* wake survives the spending shell dying. |
| 14 | **Composition**: ruflo claims + agentbbs comms + metaharness + ruvector + autogenous-service | lane roster + `peer_review` + MEM/Graphify KL + (Brief 2's RuVector) | **HAVE (arch parity) / SKIP (their service mesh)** | Same shape; ruClip embeds autogenous as a *remote service* — we would embed the *concepts*, not run their mesh (TOS + infra). |

---

## 3. The three assessments the brief asks for, head-on

**Q1 — Does autogenous's typed-mutation-with-expiry/rollback/ceiling SUBSUME our `expiry_contract` + `still_escalate_ONLY`? Where is it stricter?**
No — it is stricter on two axes and *weaker* on one.
- **Stricter (1): expiry enforces.** `admissible()` returns `Expired` when `now >= expires_at`. Ours is deliberately **REPORT_ONLY** (`clause_expiry()` returns a string; `expiry_contract.semantics`): "an expiry that revoked authority on a date would be the very defect this contract was created to fix." That divergence is principled and stays — **for standing grants.** But our *peer proposals* carry **no expiry at all**, and `expiry_contract.why` already records a proposal outliving its premise by 22h. autogenous's enforcing expiry is exactly right **there**. → increment **INC-1**.
- **Stricter (2): the ordered authority lattice + invariant typing is machine-checked per mutation.** Ours is a flat delegated/escalate set + social "chairman-only widening." We can adopt a *typed class tag* on proposals (report, not refuse) without the full genome lattice. → increment **INC-2**.
- **Weaker than us (1): reversibility.** autogenous checks `rollback_target` is present/resolvable; `peer_review --propose` **executes** `--revert-check` and requires rc∈{0,4} before the act. We are stricter; no change owed.

**Q2 — Is Ed25519 2-of-N promotion ≈ our `peer_review` (adversary≠filer + proven revert)? Gap?**
Not equivalent — **different trust models.** autogenous is a *signed quorum of measurements*: N pinned judges independently measure and sign, trust rides on Ed25519 pinned keys, worst-case-across-judges + Wilson CI guard against a lone bad judge. `peer_review` is *adversarial falsification*: one sibling must **run a refutation and fail**, with a positive control proving the probe can discriminate — and `peer_review.py`'s own header says we chose this *over* quorum because "the ledger's dominant failure is AGREEMENT, not disagreement." So the 2-of-N quorum is the model we **deliberately rejected** (and `peer_mechanism_health.py` measures ours is not a rubber stamp: 53.8% falsification). **The real gap is identity, not quorum:** CHAOS-C7 in `peer_review.py` records that the ring "was documented in prompts but not enforced, so a typo'd `--lane` still cleared" — adversary≠filer is enforced on a **string** taskId, not a key. → bounded steal: sign the `(proposal, falsification, revert-proof)` tuple for non-repudiable, tamper-evident attribution. → increment **INC-3**. The full quorum path = **SKIP**.

**Q3a — Is ruClip's approval-gate state machine cleaner than our clause-dispatch in `may()`?**
It's cleaner *as a state machine*, but it is not doing `may()`'s job. `may()` is a **classifier** (grant / escalate-clause / unknown-raises), not a transition function, and should stay one. Our actual state machine is `peer_review` (PROPOSED→CLEARED/FALSIFIED→ACTED→COMPLETE), whose guards are proven but **scattered** (`if d["state"] != "PROPOSED"` checks). ruClip's two transferable ideas: a **declarative legal-transition table** and **Guard A** (recompute legality from first principles rather than trust a passed-in object). Low-risk clarity + defence-in-depth, not new capability. → optional increment **INC-5**.

**Q3b — Is ruClip's budget-gated heartbeat better than our spend ceilings + `creds_runway.py`?**
Partly, and in exactly the dimension we have an open wound. ruClip re-checks the budget **on the cadence loop itself, fail-closed, and pauses the durable schedule** — enforcement does **not** depend on the spending process staying alive. That is the shape of the guard `authority.json` says we still lack: `above_the_ceilings` stays FOREVER_HELD specifically because "a ceiling that is only as alive as the process watching it is not a ceiling" (the `enrichment_ab_v1` wave billed 2.06× its own declared cap after its shell died — FU-342). **Honest limit:** ruClip gates the *decision to wake/spend*, not an already-burning wave, so it is a **partial** cure (it stops the *next* cycle, not the in-flight one). Adopt it as a **new out-of-process sentinel organ**, edge-triggered (fire once per incident, matching our `daily_digest`/`forever_held_conduct` alarm-fatigue rule) — **not** a clause change, and explicitly **not** licence to widen `above_the_ceilings` (chairman-only, out of scope for this brief). → increment **INC-4**.

---

## 4. Proposed adoption increments (each with expiry + rollback)

These are **proposals for the chairman to authorize**, ordered by value/cost. None is applied by this brief. Each is scoped to be a **narrowing or report-only** add — **no authority clause is widened**. Where an increment touches `peer_review.py`/`authority.py` behaviour, it ships behind the existing controls (peer clearance, `--self-test` negative controls, green CI).

| Inc | What | Steal from | Touches | Widens a clause? | Expiry (review-by) | Rollback |
|---|---|---|---|---|---|---|
| **INC-1** | Enforcing `expires_at` on a **peer proposal**: `--act`/`--sweep` refuse an expired proposal (admission-time, mirrors `agl-types` Rule 4). A *narrowing*. | autogenous #1,#5 | `peer_review.py` only | No | 2026-10-15 (next envelope `review_by` window) | Remove the field check; restore `peer_review.py` from `_followup_backups/<date>/`. |
| **INC-2** | Typed `scope` + `authority_class` **tag** on a proposal; `authority.py`/`peer_review.py` **report** (never refuse) when a proposal's requested class exceeds the clause ceiling. | autogenous #1,#2 | `peer_review.py`, `authority.py` (report path) | No (report-only) | 2026-10-15 | Drop the tag fields + the report line. |
| **INC-3** | Non-repudiable identity: sign the `(proposal, falsification, revert-proof)` tuple (Ed25519 key from AgentVault) so a clearance is cryptographically attributable; `--status` reports an unsigned/badly-signed decision. | autogenous #6 (identity only) | `peer_review.py`; key in AgentVault | No | 2026-11-05 (larger; re-review the key-management cost) | Stop requiring/printing signatures; decisions remain valid by taskId as today. |
| **INC-4** | Fail-closed, **edge-triggered budget-sentinel lane**: re-reads `vast_spend.py --summary` each tick, **pauses** paid lanes on HARD_STOP, alerts once per incident edge. Partial cure for FU-342. | ruClip #13 | new lane + tool (new organ) | No — does **not** touch `above_the_ceilings` | 2026-11-05 | Disable the lane; delete the tool; no envelope state to restore. |
| **INC-5** | (Optional) Declarative legal-transition table + recompute-legality **Guard A** in `peer_review.py`. Clarity + defence-in-depth. | ruClip #10 | `peer_review.py` | No | 2026-10-15 | Revert the refactor; behaviour is unchanged by design. |

**Deliberate SKIPs (so the chairman sees what was *not* taken):** full 2-of-N signed-quorum promotion (we chose adversary over quorum); the full genome-lineage authority lattice (no parent-genome model); 2-of-N constitutional-change signers (single chairman apex; `loco_chairmanis` + `peer_review` are our N); Company/Goals/Issues multi-tenant hierarchy (wrong domain); running `autogenous-service`/ruClip's service mesh (TOS risk on Max + infra cost — the briefs' standing constraint).

**Guardrails honoured:** design only, no code changed; no clause, ceiling, or disposition moved; `data_deletion` and `above_the_ceilings` remain FOREVER_HELD; every increment is a narrowing or report-only add behind existing controls, authorised individually by the chairman before any code is written.

---

## 5. Record

- Gap Register: `LOCO_CHAIRMAN.md` **GR-25** (grade **C0** — captured/design-only; cap C4 per authorized increment).
- To be mirrored to `FOLLOWUPS.md` as an FU with a `verify:` predicate pointing at this doc, and re-exploded to MEM (`explode_followups_to_memory.py`) per the `cooperation_contract` surface-ownership rules.
- Cross-check: an earlier session reached the same "steal autogenous's authority-ceiling + min-semantics fitness" bottom line; this doc re-derives it from source (`self_knowledge`: precedent admissible, verified against current `main`).
