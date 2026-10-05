# STATE OF PLAY — zo fleet (chairman-facing)

The one succinct place to see **what actually needs resolving**. Not the Gap Register
(`LOCO_CHAIRMAN.md` §6, formal/append-only) or `QUEUE.md` (machine-facing) — this is the
**human read**: a few ranked bullets in plain words, across all lanes/tasks/sessions. The daily
brief surfaces the top of this list; sessions keep it current (see "How this file works").

_As of 2026-10-05 (UTC). Owner tags: **[you]** = chairman decision/action · **[lane]** = a session/lane does it._

## Loose ends — ranked, resolve top-down
1. **Scoring is forking on Fly — consolidate onto the `*-sft` repo.** We can't keep scoring
   different things in different places; the axis/scoring pipeline should live in the sft repo,
   not diverge on Fly. **[you]** confirm the exact repo name + the one canonical scoring path →
   then it becomes a Gap Register row. *(new direction, 2026-10-05; biggest item.)*
2. **Budgets need sign-off + a vast top-up.** Proposed monthly: vast **$13**, Clef **$2**, IPQS **$0**
   (right-sized from padded $20/$10/$99). vast balance is **$5.50** — below run-rate, top up.
   **[you]** approve → **[lane]** lands the Reserved rows. (`_staging/budget_proposal_vast_clef_ipqs.md`.)
3. **`_tools` checkout is dirty** (~19 uncommitted edits, fleet autopoiesis in-flight). Blocks a clean
   ff and scheduling any new tower task. **[lane]** the owning lane must commit/PR its edits.
4. **GR-24 events store: C4 blocked on #3.** Store is live + at C3; daily `ZoEventsTick` + alert wiring
   wait on `_tools`. **[lane]** once #3 clears. (Distinct from the old `ZoChainTick`.)
5. **Payment rails — do NOT auto-integrate.** WaldenPay scanned hard-no (auto-OTP, zero accountability
   infra); Coinbase/Stripe legit but conflict with the attended-only spend rule. **[you]** decide if any
   is ever wanted; default = none.
6. **GR-23 staging_drain: promoted = 0.** Tick not running on the tower; batch 1 needs a real boot test +
   prod evidence before anything is "promoted". **[lane]**
7. **IPQS stays off.** 0 remote-endpoint population to grade; free key is **not** resolvable via
   `fetch_secret.py ipqs` (AGENTVAULT_MISS). **[you]** confirm where the key lives + whether a real
   remote-endpoint population will exist.
8. **ZoMorningBrief is failing** (LastTaskResult 1). It's the existing daily brief — fix or repurpose it
   to emit the top of THIS file. **[lane]**
9. **Clef / axis eval session** can run once its $2/mo is approved (token is Tower-only, AgentVault
   `cloudflare`). **[you]** approve → **[lane]** runs.
10. **The "manifest" weekend idea is still unrecovered** (cloud-session only, not Tower-indexed; "spineful"
    was recovered, in `EVENTS_STORE.md`). **[you]** if it matters, paste the Claude Docs link; else drop it.
11. **GR-25: autogenous/ruClip adoption — which rigor increments to take?** Design-only sketch (PR #6170, GR-25)
    deep-parsed both from source: we're equal-or-stricter on reversibility/hard-gates/ledger/adversary-over-quorum;
    3 real gaps worth a bounded steal (enforcing expiry on peer *proposals*; Ed25519 identity on a peer decision;
    a fail-closed budget sentinel — partial cure for FU-342). **[you]** rule on INC-1..5; none widens a clause, no
    code until authorized. (`docs/DESIGN_AUTOGENOUS_RUCLIP_ADOPTION_2026-10-05.md`.)

## How this file works
- **Before a session/lane ends**, it adds or updates **its own** loose ends here: one plain line each —
  what's unresolved · the next action · an owner tag. Resolve one → move it to "Recently closed" (one line),
  don't let the list grow.
- **Ranked by what needs resolving soonest.** Plain words, no ID-only lines; link to the Gap Register /
  `FOLLOWUPS.md` for detail. Succinct beats complete — this is the read, not the record.
- **The daily brief reads the top N here** and pings the chairman. If a Gap Register row is stale >30d or a
  docked decision >14d, it also surfaces (that's the existing consultation contract).

## Recently closed (one line each; trim weekly)
- 2026-10-05 — GR-24 events store armed on the tower → **C3** (zo-sentinel #6164; tower baseline + idempotent re-run + `check` green).
