# LOCO CHAIRMAN — the locum of truth

**Standing:** This document is the chairman's proxy. When no operator is present, it
speaks with the operator's authority on one subject: **what counts as finished.**
It exists because the mesh has proven, repeatedly and measurably, that it is
excellent at *capturing* problems and poor at *retiring* them. The ledger fills,
the graphify KL grows, the merge audits are thorough — and the same classes of
gap are rediscovered months later with new names.

**Consultation contract (non-optional):**

1. **Read this file at session start**, after `CLAUDE.md`, before choosing work.
2. **Before declaring anything done, fixed, closed, or resolved** — in a commit
   message, a PR body, a session log, a ledger entry, an issue comment — state
   its **closure grade** (§3). If you cannot state the grade, you do not yet
   know whether it is done, and you may not use the word.
3. **Before ending a session**, update the Gap Register (§6). Every gap you
   touched moves grade or records why it could not. No silent drops. A gap that
   scrolls out of context is still open; this file is where it survives.
4. **When you find something new**, name its gap class from the vocabulary (§4).
   If no class fits, add one — the vocabulary is itself autopoietic and this
   file is its body.
5. This file outranks momentum. "I was in the middle of something" is not a
   reason to skip §2's interrogatives on a live finding. The whole failure mode
   this document exists to kill is *noticing in the moment and moving on*.

This is not process theatre. Every rule above is the negative image of a real
loss this project has already taken, cited below by artifact. Read the
citations; they are the argument.

---

## 0. The diagnosis: capture without closure

The evidence that capture ≠ closure, from this repo's own record:

- **The G6 truncation.** `write_service` silently appends `LIMIT 200` and
  reports `count` as rows *returned*, not rows *matched*. Found 2026-08-23
  (`docs/MERGE_AUDIT_2026-08-23.md`), mechanism fully understood, patch written
  and verified against a stub the same week
  (`ops/zo_mesh/write_service_g6_truncated_flag.patch`,
  `docs/G6_BUS_TRUNCATION_CALLERS.md`). As of the last audit pass it was still
  marked **"Not yet applied"** — parked on issue
  [#3997](https://github.com/rob531/zo-sentinel/issues/3997) with
  `needs-decision`, where nothing re-asks the decision. Meanwhile every caller
  census entry in G6 keeps reasoning about a partial database.
- **AP-001.** The builder called `write_service()` as a Python function in
  **5 of 11** failed builds across **six days and five distinct tasks**
  (`BUILDER_ANTIPATTERNS.md`). The pattern was logged each time. Six days of
  identical failure is not a hard bug; it is a feedback loop that captures
  without steering.
- **The dead generator.** `sentinel_directive_generator.py` died and went
  **23 days unnoticed** (`CLAUDE.md`, builder self-repair harness motivation).
  The system that emits work had no watcher, and nothing in the daily loop
  asked "is the emitter alive?"
- **The retry sweep.** `RETRY_GAP_SWEEP.md` (2026-04-21) fixed the discovery
  site, then listed **ten sibling files** under "not yet scanned (likely have
  same gaps)". That list is the sweep that was never swept — a census written
  down as a substitute for being executed.
- **The un-closeable follow-ups.** Seven FU acceptance predicates query tables
  that do not exist on the bus (finding **B1**). Because `sql_assert` is
  three-state, a catalog error lands in **UNKNOWN**, not RED — so the very
  mechanism that decides whether follow-ups can close reports *unknown
  forever* and nobody is paged. Governance reads a signal that can never
  resolve.
- **The delegated gate.** tier4 skips `import app.main` "owned by tier1";
  tier1's allowlist contains **zero** `app` modules (finding **G1**). Each gate
  believed the other held the surface. Coverage existed only by accident, via
  two root-level modules that happened to import `app.db` transitively.
- **The report-only ratchet.** `referent_verify` columns check: **114 missing
  of 366**, verdict FAIL — and unarmed (`docs/STATUS_2026-08-28.md`). A number
  that is measured but does not block is a number the mesh has learned to read
  past. (Contrast: tables went 18 → 0 *and then got armed* — #4124 — and
  disarming was made as hard as arming — #4128. That pair is the model.)
- **The deferred graveyard.** The deferral list stands at **63 against a cap
  of 40**; the escalation the cap exists to force is "still owed"
  ([#4005](https://github.com/rob531/zo-sentinel/issues/4005)).
  `PRODUCT_SPEC.md` itself names the failure mode: *"'deferred' quietly becomes
  the new graveyard with a nicer name, which is exactly what happened to the
  last list that let work be postponed without a date."*
- **The Potemkin era.** 559 files emitted, 76% of the lines, 13% of the app,
  **2.9% yield** (`AUTOPOIESIS.md`). Production without self-production. The
  loop was fixed by changing what counts (spineful emission, the ratchet, the
  liveness contract) — not by emitting harder. That is the template for
  everything in this file.

The through-line: **every one of these was known at the moment it mattered.**
The knowledge was in a log, a ledger row, a KL node, an audit section. What was
missing was not detection. It was an authority that refuses to let a known
thing stay half-done, and a vocabulary precise enough that the half-done state
is *visible as a state* rather than dissolving into "we'll get to it."

This file is that authority. The vocabulary follows.

---

## 1. What "the locum of truth" means operationally

A locum holds the practice while the principal is away — with full authority,
not advisory authority. Concretely:

- **Truth here is measured, never asserted.** The standard is
  `docs/STATUS_2026-08-28.md`: *"every claim names the command or the file that
  produced it."* A claim in a session log or PR body that names no command and
  no file is an opinion, and opinions do not close gaps.
- **The locum's one power is the definition of done.** It cannot merge, deploy,
  or decide `needs-decision` items — those stay with the operator. What it can
  do, and does, is refuse the word *closed* to anything below grade C4 (§3),
  and refuse the word *unknown* as a resting state (§4, GC-1).
- **The locum re-asks parked decisions.** A `needs-decision` label is a
  question to a human, and questions decay. Every session that touches this
  file checks the Decision Dock (§6.2): any decision parked longer than its
  re-ask cadence gets surfaced again — in the session summary, at the top, with
  the cost of continued deferral stated in measured terms. Silence is never
  treated as an answer.
- **The locum is itself subject to the rules.** Its Gap Register entries carry
  grades and evidence like anything else. If this file's claims drift from the
  measured state of the repo, fixing this file *is* pipeline work, at P0.

---

## 2. The chairman's interrogatives

These are the questions that find the hidden edge *in the moment*, before it
becomes next quarter's rediscovery. Run them at two trigger points, without
exception:

- **On any new finding** (a bug, a failed check, a surprising number, a log
  line that makes you pause), before writing the fix.
- **On any fix, before it merges.**

The questions, each the negative image of a loss already taken:

1. **Who else has this bug?**
   The fix site is almost never the only site. Enumerate the sibling census
   *and execute it in the same change or the same session* — a census written
   down for later is `RETRY_GAP_SWEEP.md`, and its "later" never came. A fix
   that merges without its sibling sweep is grade **C2 at best**, and the
   register entry stays open saying so.

2. **What does this failure look like when it is silent?**
   Trace the failure to its resting state. Does it land in RED (someone acts),
   or in UNKNOWN / empty-list / zero-rows / caught-and-logged (nobody acts)?
   B1's predicates rest in UNKNOWN forever. G6's truncation rests in a
   plausible-looking `count`. The catch-all middleware rests in an empty table
   that looks like "no data." Every silent resting state you find is itself a
   finding: file it.

3. **Which gate would have caught this — and is it armed?**
   Name the specific required check that should have gone red. If none exists,
   the fix is incomplete by definition (§3, C4). If one exists but is
   report-only, arming it is part of this fix, not a follow-up. If two gates
   each think the other owns the surface (G1), you have found a delegated
   gate: collapse the delegation now.

4. **Can the gate be quietly disarmed?**
   #4128 is the precedent: after arming the tables check, a test was added
   that fails if `,tables` is deleted, if a `paths:` filter appears, if the job
   is renamed, or if `continue-on-error` shows up. An armed gate that can be
   disarmed in a one-line diff nobody reads is armed only until it is
   inconvenient. Harden the disarming path.

5. **What is this interface not telling its caller?**
   G6's lesson generalised: any surface that mutates the question (injects a
   LIMIT, strips a field, falls back to a stub, substitutes a cached answer)
   and does not say so in the response is manufacturing false confidence
   downstream. `zobridge` falling back to stubs "keep going either way" is
   fine for iteration and poisonous for measurement — any number destined for
   a report, a verdict, or this file must state which plane produced it.

6. **Where is the decision parked, and when was it last re-asked?**
   Every `needs-decision` item goes in the Decision Dock with a re-ask cadence.
   The G6 patch sat fully-written because the decision had a parking spot and
   no meter.

7. **Does the watcher have a watcher?**
   23 days. Any component whose job is to notice things must itself be noticed
   within one cycle of dying: heartbeat to `service_health`, staleness alarm
   that does not live on the host it watches (the #4122 pattern). When you add
   a monitor, add its monitor in the same change.

8. **Is the number moving, or is the class retiring?**
   Many small merges that each decrement a counter can coexist with a class
   that is not shrinking (AP-001, five occurrences, six days). Ask of any
   recurring metric: what single structural change would make this *class*
   impossible? If the answer is known and unbuilt, that is the real backlog
   item, and the counter-decrements are maintenance, not progress.

9. **Does the fix teach the emitter?**
   The autopoietic standard (`AUTOPOIESIS.md`): the loop teaches its own
   emitter at the moment of failure (redirect-reject), heals its own emissions
   (casing-repair). A fix a human (or Claude) applies downstream, that the
   directive generator / builder prompt never learns, will be regenerated as a
   bug by the next emission. If the root cause is in generated code, the fix
   lands in `BUILDER_ANTIPATTERNS.md` (with `detect_pre`/`detect_post`) *and*
   in the emitting prompt — or it is not fixed.

10. **What would the merge audit say?**
    The 2026-08-23 audit found its gaps by asking what the required checks do
    *not* catch and what unattended merges assumed. Apply the same adversarial
    read to your own diff before pushing: which of B1/G1/G6's shapes does this
    change risk reproducing?

Running all ten takes minutes. Every one skipped is a coin-flip on a
rediscovery that will cost a session.

---

## 3. Closure grades — the only definition of done

Every gap, finding, ledger entry, and register row carries exactly one grade.
The grade is a claim about evidence, not effort.

| Grade | Name | Meaning | Evidence required |
|---|---|---|---|
| **C0** | Captured | Observed and written down (ledger, KL, issue, audit). | The artifact that records it. |
| **C1** | Patched at site | Symptom fixed where discovered. Siblings unswept, root cause unnamed or unaddressed. | The merged diff. |
| **C2** | Root-caused | The originating mechanism is named and fixed at the origin (emitter, prompt, schema, contract) — not just the site of the symptom. | A written root-cause statement naming the mechanism, plus the diff at the origin. |
| **C3** | Class swept | The sibling census was enumerated **and executed to zero** — every site of the class fixed or explicitly registered as its own gap. | The census (command + file list) and its zero-remainder result. |
| **C4** | **Closed** | An armed gate makes recurrence a red build, and disarming the gate is itself gated (#4128 pattern). Where applicable, the emitter has been taught (interrogative 9). | The gate's name in required checks; the disarm-hardening test; the antipattern/prompt diff if generated code was involved. |

Rules:

- **Only C4 may be called "closed" / "resolved" / "done."** C1–C3 are honest,
  useful, reportable states — call them by their grade.
- A grade may only be claimed with its evidence column satisfied. "I'm
  confident it's fixed" is C0 of a new gap: *unverified confidence*.
- Some gaps legitimately cap below C4 (a one-off data correction has no class
  to gate). The register row then says `cap: C2, reason: …` — an explicit
  ceiling, decided once, not a quiet stall.
- The historical failure mode this table exists to end: **the project's median
  fix is C1 and its ledgers cannot see the difference between C1 and C4.**
  From now on they can.

---

## 4. The gap-class vocabulary

Names for the shapes that keep escaping. Use them in ledger entries, KL nodes,
issue titles, and register rows — a shared name is what lets the fifth
occurrence be recognised as the fifth, not the first. Each class lists its
canonical instance and its standard kill.

**GC-1 · The unknown-not-red hole.**
A three-state (or n-state) check where the failure mode rests in a state
nothing escalates. *Canonical:* B1 — catalog errors land in UNKNOWN; seven FUs
un-closeable forever, silently. Also: parse coverage UNKNOWN in
`referent_verify`. *Kill:* every UNKNOWN gets a budget (age or count); an
UNKNOWN older than budget **is** RED. No verdict state may be unreachable by
escalation.

**GC-2 · The delegated gate.**
Two gates each recording a surface as the other's responsibility; coverage
exists only by accident. *Canonical:* G1 — tier4 defers `app.main` to tier1,
whose allowlist holds zero `app` modules. *Kill:* ownership is written in one
place, and a test asserts every declared surface has exactly one owning gate.
"Skipped: owned by X" lines must be verifiable claims, checked against X's
actual manifest.

**GC-3 · The report-only ratchet.**
A check that measures and never blocks; the number becomes wallpaper.
*Canonical:* columns 114-missing, FAIL, unarmed. *Kill:* every report-only
check carries an arming condition ("arm when ≤ N" or "arm by date D") recorded
in the register. A check with no arming path is a dashboard, not a gate — say
which it is.

**GC-4 · The partial sweep.**
Pattern fixed at the discovery site; the sibling census written down as a
to-do and never executed. *Canonical:* `RETRY_GAP_SWEEP.md`'s ten unscanned
files. *Kill:* interrogative 1 — the census executes in the same change or the
same session, or the register row stays open at C1/C2 naming the remainder.

**GC-5 · The silent mutation.**
An interface that alters the question (injected LIMIT, stripped field, stub
fallback, cache substitution) without declaring it in the answer. *Canonical:*
G6 — `LIMIT 200` injected, `count` = rows returned, truncation invisible.
*Kill:* the response carries the mutation flag (`truncated`, `plane: stub`,
`cache_age`); callers are censused (the G6 doc's second half is the exemplar)
and taught to check it.

**GC-6 · The vocabulary drift.**
Two live dialects for one concept; aliasing papers over the split and the
split keeps generating work. *Canonical:* documented verdicts
(`TRUSTED_GENERAL` … `INSUFFICIENT`) vs production-emitted (`unknown` ~82%,
NULL, mixed case) — `CLAUDE.md` itself must carry an alias table to bridge
them. *Kill:* one vocabulary is declared canonical in one file; every emitter
is migrated or wrapped with a dated shim; a gate rejects new emissions in the
dead dialect.

**GC-7 · The deferred graveyard.**
A postponement mechanism without dates, caps, or a consumer — postponement
becomes the destination. *Canonical:* deferral list at 63 vs cap 40,
escalation still owed (#4005); before that, the orphan backlog
(`ORPHAN_REVIEW_FABLE5.md`). *Kill:* every deferral carries a date and a
reason; an aging report runs on cadence
(`deferred_router_ledger_report` pattern); breaching the cap *fires the
escalation automatically* instead of owing it.

**GC-8 · The echo verification.**
A test that verifies the system against the system's own description of
itself — self-fixture, self-schema, self-log — and therefore cannot see the
referent drift. *Canonical:* `audit_log_api` querying its own self-test's
schema, not the real one (#4127); the evidence_density smoke false pass
(`INCIDENT_evidence_density_v1_smoke_false_pass.md`). *Kill:* verification
reads the live referent (real catalog, real bus, clean worktree of
`origin/main` — the `referent_verify` discipline) or is labelled a unit test,
never a proof of integration.

**GC-9 · The needs-decision stall.**
Work fully prepared, blocked solely on a human decision that has a parking
spot and no meter. *Canonical:* the G6 patch — written, stub-verified,
documented, unapplied (#3997). *Kill:* the Decision Dock (§6.2). Every parked
decision has a re-ask cadence; each re-ask restates the measured cost of
continued deferral. Decisions may be made "no" — they may not be made by rot.

**GC-10 · The unwatched watcher.**
A monitoring component with no monitor; the detection system's own death is
the one thing it cannot detect. *Canonical:* the directive generator's 23
silent days; the builder sort crash that ran for hours. *Kill:* heartbeats to
`service_health` (30s contract), staleness alarms hosted off the watched box
(#4122 pattern), and the watchdog trio (`process_watchdog`,
`builder_health_checker`, `directive_queue_guardian`) kept under supervisord
with `autorestart=true`. New monitor ⇒ its monitor, same change.

**GC-11 · The unteaching fix.**
A defect in generated output fixed downstream while the emitter keeps
emitting it. *Canonical:* AP-001, five identical failures, six days — "strong
evidence the directive_generator is not steering away from it" is the
antipattern file's own wording. *Kill:* interrogative 9 — the fix is not C4
until `BUILDER_ANTIPATTERNS.md` carries the pattern with `detect_pre` and
`detect_post`, and the emitting prompt carries the steer.

**GC-12 · The counter treadmill.**
Sustained activity on a metric with no structural change that retires the
class; effort is real, the class is immortal. *Canonical:* the Potemkin
ladder — 559 files, 2.9% yield — cured only when the definition of output
changed (spineful emission). *Kill:* interrogative 8 asked explicitly in
review: "what one change makes this class impossible?" — and that change
scheduled, or its absence justified in writing.

When a new shape appears that none of these fit, **add GC-13** with its
canonical instance and kill. A gap class without a name will be rediscovered;
that is this project's most reliably reproduced experimental result.

---

## 5. The finish line: autopoiesis → product → revenue

"Done" for the project, not just for a gap. The project is finished when a
stranger can pay for an MCP risk verdict, trust it, and the system that
produced it maintains itself. That decomposes into five stages, strictly
ordered — each stage's exit criteria are measurable, and per §3, a stage is
closed only when its gates are armed, not when its numbers look good one day.

**S0 — Pipeline honesty.** *The system may not lie to itself.*
Exit: `referent_verify` all four checks armed (routes ✅, tables ✅, columns
and parse-coverage pending — GR-3); zero GC-1 resting states in the closure
machinery (B1 fixed — GR-6); G6 flag deployed and callers checking it (GR-1);
deferral list under cap with auto-escalation live (GR-5); the builder-lane
contention resolved (one lane, or partitioned lanes —
`docs/FINDINGS_2026-08-23.md` §1). Everything downstream inherits its floor
from S0; skipping ahead of it rebuilds Potemkin.

**S1 — Signal integrity.** *The verdicts mean something.*
Exit: one canonical verdict vocabulary, emitters migrated (GC-6 killed —
GR-7); the `unknown` share (~82% of `mcp_server_registry`) driven down by
enrichment that is *measured against labelled ground truth*, not by
re-labelling; all twelve canonical signal types flowing with the invariant
contract (`signal_type`, `confidence`, `evidence_blob`, `server_id`,
`scored_at`) enforced by a gate, not a convention; scoring-axis changes
validated against `RISK_CURVE_HYPOTHESIS.md`-style backtests before deploy.

**S2 — Product surface.** *An analyst chooses to use it.*
Exit: the `CLAUDE.md` UI backlog P0–P3 complete (stability, at-a-glance
posture, filtering, drill-down); the external API path
(`sentinel_external_api.md`) serving real queries with the G6-honest contract;
"why untrusted" explanations rendered from signal breakdowns — the verdict is
inspectable, which is the product's actual moat over a static list.

**S3 — Trust externalisation.** *A stranger can verify us.*
Exit: attestations and scan history public per server; the badge trust model
(`docs/DESIGN_BADGE_TRUST_MODEL_2026_07_12.md`) live including the dispute
ledger — *auditable fallibility is the trust product*; verdict drift surfaced,
not hidden. We sell judgment with receipts; the receipts are the feature.

**S4 — Commercial operation.** *Somebody pays, repeatedly.*
Exit: a priced offer (API tier / feed / badge program) with its cost floor
known from the spend ledgers (`scoring_wave_cost_ledger`,
`wedge_spend_ledger_report`); uptime honest via `service_health` with GC-10
kills in place; first external revenue booked; churn and verdict-accuracy
metrics reviewed on the same cadence as this file.

**Autopoiesis, throughout:** at every stage the loop must keep producing
itself — directives emitted from the system's own self-description, builds
regenerating that self-description, gates refusing allopoietic waste
(`AUTOPOIESIS.md`). A commercially successful tool that requires nightly
manual rescue has not finished S0, whatever its MRR says.

---

## 6. The Gap Register and the Decision Dock

### 6.1 Gap Register

The persistent memory this document exists to enforce. Rules: rows are
**append-and-close** — a row leaves this table only by reaching its grade cap
with evidence, never by deletion; every session that touches a row updates
`last-touched`; a row untouched for 30 days is raised in the session summary
by whoever notices (that noticing is mandatory — it *is* the consultation
contract).

Seeded 2026-08-29 from the measured record:

| ID | Gap | Class | Grade | Cap | Evidence trail | Last touched |
|---|---|---|---|---|---|---|
| GR-1 | G6 silent truncation: patch written, unapplied; caller census not yet checking `truncated` | GC-5, GC-9 | **C2** | C4 | `docs/G6_BUS_TRUNCATION_CALLERS.md`, patch in `ops/zo_mesh/`, [#3997](https://github.com/rob531/zo-sentinel/issues/3997), [#4003](https://github.com/rob531/zo-sentinel/issues/4003) (16 schema enumerations + 583 unpaginated row reads) | 2026-08-29 |
| GR-2 | Delegated gate on `app.main` (tier1/tier4 mutual deferral) | GC-2 | **C0** — demonstrated by defect injection; kill not yet verified merged | C4 | `docs/MERGE_AUDIT_2026-08-23.md` G1 | 2026-08-29 |
| GR-3 | Columns referent check: 114 missing, FAIL, report-only; parse coverage UNKNOWN on 4 modules | GC-3, GC-1 | **C0** | C4 (arm + harden disarm, per #4124/#4128 pattern) | `docs/STATUS_2026-08-28.md` §1a | 2026-08-29 |
| GR-4 | Retry/backoff + reasoning-strip gaps: 10 sibling files censused, never swept | GC-4 | **C1** (discovery site patched 2026-04-21) | C3 | `RETRY_GAP_SWEEP.md` "files not yet scanned" | 2026-08-29 |
| GR-5 | Deferred list 63 > cap 40; escalation owed, not automatic | GC-7 | **C0** | C4 (auto-fire on breach) | [#4005](https://github.com/rob531/zo-sentinel/issues/4005), `PRODUCT_SPEC.md` deferred_router_triage_report candidate | 2026-08-29 |
| GR-6 | 7 FU predicates query nonexistent tables; UNKNOWN unescalated in `sql_assert` | GC-1 | **C0** | C4 (fix names + UNKNOWN age budget) | MERGE_AUDIT B1, `tools/fu/fu_seed_predicates.py` | 2026-08-29 |
| GR-7 | Verdict vocabulary drift: docs vs production emitters (`unknown` ~82%, NULL, case) | GC-6 | **C1** (alias layer papers over it) | C4 (canonical vocab + emitter migration + gate) | `CLAUDE.md` verdicts reference | 2026-08-29 |
| GR-8 | `model_import_linter --fix` corrupts SQL table names in string literals; blocks 34 of 63 triage decisions | GC-12 (blocker breeding blockers) | **C0** | C3 | [#4000](https://github.com/rob531/zo-sentinel/issues/4000), [#4004](https://github.com/rob531/zo-sentinel/issues/4004) | 2026-08-29 |
| GR-9 | Builder-lane contention: two lanes contesting the same directives; archival record says retired, retirement never took effect | GC-8 (record ≠ referent) | **C0** | C4 | `docs/FINDINGS_2026-08-23.md` §1.4–1.8 | 2026-08-29 |
| GR-10 | 6 `dependency_overrides` sites import a callable that exists nowhere; 25 staged services fail dry-run import | GC-4 | **C0** | C3 | [#4001](https://github.com/rob531/zo-sentinel/issues/4001), [#4002](https://github.com/rob531/zo-sentinel/issues/4002) | 2026-08-29 |

Grades above are conservative on purpose: several of these have had work
merged since their source audits, but **per §3 a grade is claimed with
evidence, and re-verification against current `main` has not been done here.**
First session to touch each row: re-measure, then move the grade with the
command that proves it. That re-measurement is itself the model behaviour this
file mandates.

### 6.2 Decision Dock

Where `needs-decision` items wait *with a meter running*. Re-ask cadence
default: **every session that reads this file** mentions any docked decision
older than 14 days in its summary, restating the measured cost of deferral.

| Decision | Docked since | Owner | Cost of deferral (measured) |
|---|---|---|---|
| Apply G6 patch + restart `write_service` ([#3997](https://github.com/rob531/zo-sentinel/issues/3997)) | 2026-08-23 | operator | Every unpaginated caller (583 row-read sites, #4003) keeps reasoning over a silently truncated store; one confirmed instance read 200/25 where truth was 355/44 |
| Fast-forward build workspace — 46 daemon files block ([#3998](https://github.com/rob531/zo-sentinel/issues/3998)) | 2026-08-23 | operator | Build workspace diverges from `main`; referent checks must run in a clean worktree to be honest (STATUS §1a notes exactly this) |
| `app/scoring_consumer.py` — no callers, delete? ([#3999](https://github.com/rob531/zo-sentinel/issues/3999)) | 2026-08-23 | operator | Dead matter accrues; the janitor doctrine (`AUTOPOIESIS.md`) says retire it |
| Triage the 63 deferrals — 12 RETIRE need approval ([#4004](https://github.com/rob531/zo-sentinel/issues/4004), [#4005](https://github.com/rob531/zo-sentinel/issues/4005)) | 2026-08-23 | operator | Cap breached by 23; every week the graveyard normalises further |

---

## 7. Session protocol (the loop, amended)

The `CLAUDE.md` improvement loop stands. This file adds three beats to it:

```
SESSION START
  read CLAUDE.md, then LOCO_CHAIRMAN.md
  scan Gap Register: any row stale >30d? any docked decision >14d?  -> queue for summary
WORK (per CLAUDE.md loop)
  on any new finding:            run §2 interrogatives, assign a GC class
  before any "done"/"fixed":     state the closure grade, with evidence
  before any fix merges:         interrogatives 1, 3, 4 answered in the PR body
SESSION END
  update touched Gap Register rows (grade moved, or why not)
  append SESSION_LOG.md per CLAUDE.md
  summary surfaces: stale rows, docked decisions, any new GC class minted
```

Three prohibitions, stated once, flat:

- **Never** report a gap as closed at a grade below C4 — use the grade's name.
- **Never** write a sibling census as a to-do; execute it or register the
  remainder as an open row the same day.
- **Never** let an UNKNOWN rest without an age budget. Unknown is a countdown,
  not a category.

---

## 8. Amendment

This file is amended the way everything else here is: by PR, with evidence.
Add gap classes freely (with canonical instance + kill). Tighten grades and
cadences freely. **Loosening** a rule — a grade requirement, a prohibition, a
re-ask cadence — requires citing the incident record showing the rule is
wrong, not merely inconvenient; §0 is the standing rebuttal to
inconvenience.

*Companions: `AUTOPOIESIS.md` (the loop's naming doctrine and the bar for
spineful emission), `BUILDER_ANTIPATTERNS.md` (the emitter's taught record),
`docs/MERGE_AUDIT_2026-08-23.md` + `docs/STATUS_2026-08-28.md` (the measured
state this register was seeded from), `docs/RISK_REGISTER.md` (server-level
risk, distinct from this process-level register).*


---

## 9. Staged self-consumption — how Claude Code feeds this to itself

This file is long by design; reading all of it every turn is a token tax that
would itself become a reason to skip it. So it is **staged**: read the layer
the moment requires, no more. The layers, cheapest first:

| Layer | File / section | When to read | ~Cost |
|---|---|---|---|
| L0 | `chairman/CHECKPOINT.md` | **First, always.** If non-empty, a prior session ran out of context mid-gap — resume it before anything else. | tiny |
| L1 | `chairman/QUEUE.md` | Session start, and whenever choosing the next unit of work. Each entry is self-contained: one gap, target grade, first command. | small |
| L2 | This file, §2 + §3 only | On any new finding, and before any "done" claim. The interrogatives and the grade table are the working core. | small |
| L3 | This file, §4 | When labelling a finding, or when a shape feels familiar — check the vocabulary before minting a description. | medium |
| L4 | This file, whole | Session start of a *governance* session (register grooming, stage review, amendment), or first contact with the project. | full |

The staging rule in one line: **L0 → L1 → work → L2 at every finding/claim →
checkpoint or register update at the end.** A session that only has budget for
one read reads the checkpoint.

`chairman/QUEUE.md` is the machine-facing feed. Its contract:

- Entries are ordered; the top entry is the default next unit of work.
- Every entry names: register row, current grade, target grade, the **first
  command to run** (so a cold session starts moving inside one tool call), and
  the evidence that will justify the grade move.
- An entry is removed only by a register grade-move with evidence; reordering
  is free, deletion without a grade move is prohibited (§7's "no silent
  drops", mechanised).
- The queue is regenerable from the Gap Register — if they disagree, the
  register wins and the queue is rebuilt from it.

## 10. Context checkpoint protocol — the low-token capture

Sessions die three ways: the context fills and summarises, the token budget
runs low, or a remote container is reclaimed. In all three, *uncommitted
understanding evaporates* — the exact mechanism behind "noticed in the moment,
rediscovered later" (§0). The kill:

**Trigger.** Any of: a context-management/summarisation notice; the visible
token budget dropping below ~10% of what the session started with (in this
harness, watch `<total_tokens>`); the work item in hand clearly outsizing the
remaining budget; or a remote session approaching an ending turn with
unpushed state.

**Action — the capture, in this order:**

1. Write `chairman/CHECKPOINT.md` (template lives in the file itself):
   - which register row / queue entry was in hand,
   - the grade being worked toward and the interrogatives (§2) already
     answered — *with their answers*, not "done",
   - evidence gathered so far: commands run **verbatim** and their key
     numbers (a future session must be able to re-run, not re-derive),
   - the exact next command,
   - anything believed-but-unverified, labelled `UNVERIFIED:` (per §3, that
     is C0 material and must not be laundered into fact by the resume).
2. **Commit and push the checkpoint.** In a remote session an unpushed
   checkpoint dies with the container; the capture *is* the commit. A
   checkpoint commit on the working branch is always in-scope, never noise.
3. Update the touched register row's `last-touched`, then stop starting new
   work — a checkpointed half-gap beats two evaporated ones.

**Resume.** The next session's L0 read finds the checkpoint, re-runs the
recorded commands to re-establish the referent (never trusts the numbers as
still-true — GC-8 applies to your own past self), continues, and **empties the
checkpoint** in the same change that moves the register row. A non-empty
checkpoint older than 7 days is escalated in the session summary like a stale
register row.

## 11. Instruments — graphify and the memory planes

Interrogatives need instruments. Two are mandated; both come with honesty
rules, because an instrument that silently answers from a stale or absent
substrate is GC-5/GC-8 wearing a lab coat.

### 11.1 Graphify — the structural eye

The graphify MCP server (`graph_stats`, `god_nodes`, `query_graph`,
`get_neighbors`, `shortest_path`, `get_community`, `triage_prs`,
`get_pr_impact`, `list_prs`) is the instrument for questions grep answers
badly:

- **Interrogative 1 (who else has this bug?)** — `query_graph` /
  `get_neighbors` around the defective node beats keyword search for finding
  sibling call sites; a sibling census should cite the graph query it ran.
- **Interrogative 3 (which gate owns this surface?)** — `get_community` and
  `god_nodes` expose ownership structure; a surface whose community spans two
  gates' manifests is a GC-2 candidate *before* it fails.
- **Interrogative 10 (what would the audit say?)** — `get_pr_impact` before
  merge: which communities does this diff touch, and were they all considered?
  `triage_prs` orders the open-PR backlog by structural risk instead of
  recency.
- **God nodes are standing risk.** The most-connected nodes are where a C1
  patch has the widest silent blast radius; changes touching them get the full
  §2 treatment by default.

**Honesty rule (measured 2026-08-29):** `graphify-out/graph.json` is a scan
artifact, ignored by git — **it does not exist in a fresh clone**, and every
graphify call errors until the scan is regenerated. So: regenerate the graph
at the start of any session that will make structural claims, or mark those
claims `UNKNOWN (no graph)` with an age budget per GC-1. A graph-derived
claim must state the graph's build time; reasoning over a week-old graph about
today's diff is echo verification. This absent-by-default instrument is
itself registered as **GR-11**.

### 11.2 Memory — three planes, one write-through order

"Remembering" happens on three planes with very different guarantees:

1. **This repository** — the register, queue, and checkpoint under git. The
   only plane guaranteed present in every session. All governance state
   writes here **first**; a memory that isn't committed does not exist.
2. **The bus** (`mesh_memory` via `:8772`, `agent_id='zo_sentinel.chairman'`)
   — the mesh-visible mirror, so host-side daemons and the directive
   generator can read chairman state without a git checkout. Written second,
   best-effort, through `ws_write` like everything else. Subject to G6
   honesty: any read back must check `truncated`.
3. **A memory MCP server, when connected** — for cross-session recall of
   observations too granular for the register (an odd log line, a hunch, a
   provider quirk). **Not connected in this session** (verified against the
   live tool roster 2026-08-29); when it is, it joins as plane 3, never
   plane 1.

The write-through order is a rule, not a preference: repo → bus → mem. And
the read rule is GC-8's: memory of any plane is a *pointer to evidence*, not
evidence. A remembered number gets re-measured before it appears in a grade
claim; the memory tells you which command to re-run, and that is its whole
job.

## 12. Beyond the prosaic past — the dev-driven future of the builder

Everything above disciplines the past. This section commits the builder to a
future, stated as capabilities with the same measurability standard as §5 —
each is where the current frontier of agentic development practice actually
is, mapped onto organs this mesh already grew. Nothing here is speculative
architecture; each item names its existing seed and its next concrete rung.

**F1 — Spec-driven regeneration.** The frontier practice: the spec, not the
file, is the unit of truth; code is a projection that can be re-emitted.
*Seed:* `PRODUCT_SPEC.md`'s directive candidates already carry
acceptance-first contracts ("ACCEPTANCE: ... prints PASS"). *Next rung:* every
new service the builder emits must be regenerable from its directive alone —
directive + acceptance in the repo, emission reproducible, drift between spec
and artifact detectable by re-emission diff. A file that can only be edited,
never regenerated, is legacy the day it lands.

**F2 — Eval-harnessed emitters.** The frontier practice: generation quality
is a measured curve, not an anecdote; emitters ship with eval sets and
regression gates. *Seed:* `BUILDER_ANTIPATTERNS.md` is a hand-grown eval set
— each AP is a failing case with a `detect_post` oracle. *Next rung:* freeze
the APs into a runnable suite the builder's prompt changes are scored
against; a prompt or model change that regresses the AP pass-rate is a red
build, exactly as #4124/#4128 did for table referents. The emitter gets the
same ratchet the code got.

**F3 — Checkpointed long-horizon autonomy.** The frontier practice: agent
work as resumable computation — explicit state capture, idempotent resume,
progress that survives the death of any single context window. *Seed:* §10 of
this file; the `.done.json` directive lifecycle. *Next rung:* the builder's
own cycles adopt the checkpoint contract — a directive interrupted mid-build
records phase + evidence and resumes, instead of restarting or (worse)
half-finishing silently. Long tasks become chains of verified checkpoints,
not marathons of hope.

**F4 — Graph-native self-description.** The frontier practice: agents reason
over a maintained structural model of the system, not over grep output.
*Seed:* the app-surface KL, `schema_kl.json`, the census/spine machinery —
autopoiesis already defines self-description as the loop's first organ.
*Next rung:* the graph is rebuilt by CI on every merge (killing GR-11's
staleness by construction), and directive emission consumes graph deltas —
"what changed structurally since the last emission" becomes an input, so the
architect proposes from the system's *current* body, not its remembered one.

**F5 — Memory-augmented emission.** The frontier practice: retrieval over
past failures and decisions at generation time, not wisdom re-read by humans.
*Seed:* the directive generator already consumes `BUILDER_ANTIPATTERNS.md`;
`mesh_memory` exists. *Next rung:* structure the recall — before emitting,
the generator retrieves the APs, register rows, and prior directives nearest
the task at hand and injects them as steering context. The 23-day blindness
and the six-day AP-001 loop were both *retrieval failures*: the knowledge
existed and was not in the window that needed it. Fix the plumbing, not the
prose.

**F6 — The autonomy ratchet.** The frontier practice: agent permissions are
earned by verification history, not granted by optimism — autonomy widens
exactly where armed gates stand. *Seed:* the liveness contract, the orphan
ratchet, the C4 grade itself. *Next rung:* make it explicit policy — the
builder may self-merge only in lanes whose failure classes are C4-gated;
every new C4 closure is simultaneously a widening of safe autonomy. This
inverts the usual fear-based throttle: **verification budget purchases
autonomy**, so closing gaps stops being hygiene and becomes how the loop buys
its own freedom. That is the incentive structure under which gaps finally
close.

**F7 — Adversarial verification lanes.** The frontier practice: the agent
that builds is never the agent that judges; verification is a separate lane
with a separate incentive (find the flaw), because self-graded work converges
on GC-8. *Seed:* rescue_smoke, the gates, the merge audit's
defect-injection method (G1 was *demonstrated*, not inferred). *Next rung:* a
standing verifier lane that injects defects and probes gates on cadence — the
audit's method, mechanised — so gate coverage is continuously measured
rather than annually discovered.

**F8 — The customer joins the loop.** The frontier practice: production
telemetry is a first-class emitter input; the product's users become sensors.
*Seed:* the dispute ledger in the badge trust model — auditable fallibility
as product. *Next rung:* analyst actions in the UI (disputes, verdict
overrides, watchlist adds) flow back as signals into the directive generator,
so commercial usage *is* self-description and S4 revenue literally feeds S0
honesty. At that point the autopoietic loop closes around the market — the
system produces itself, and the product of that production pays for the next
turn of the loop.

The prosaic past was allopoietic: effort producing artifacts. The future
committed to here is autopoietic all the way up: **specs that regenerate
code, evals that regenerate trust, checkpoints that regenerate sessions,
graphs that regenerate comprehension, gates that regenerate autonomy, and
customers that regenerate the mission.** Every gap closed at C4 is a rung on
that ladder, which is why this file refuses to let one stay half-closed.

---

## 13. The landing doctrine — solid PRs merge, or they are a gap

A pull request is **inventory, not achievement**. Work delivers value at
merge; an open PR is unrealized value that decays (base drift, conflicts,
duplicate re-emissions, reviewer context loss). The 2026-08-23 audit worked
around **117 open PRs** it could not touch; a backlog that size is not a queue,
it is a graveyard with better lighting — the same GC-7 shape wearing a
different label.

**GC-13 · The open-PR graveyard.** Solid, wanted work parked in an open PR
with no mechanism driving it to merge; the landing chain has a link that
requires a human click nobody's cadence owns, or a link that silently stops
re-evaluating. *Canonical:* the recorded 2026-07 failure in
`tools/pr_triage.py:179` — a base-branch breakage turned the whole cohort's
gates red, every PR was labelled `triage:stale`, auto-merge (armed only on
`triage:solid`) never fired, and when main recovered **nothing re-ran their
checks** — no push event ever reaches those heads, so recovery upstream left
the backlog stale forever. *Kill:* every link in the landing chain is owned by
a mechanism, none by an unowned click, and every "wait" state has a
re-evaluation trigger.

The landing chain, each link with its owner:

| Link | Owner | Status |
|---|---|---|
| Emit → PR opened | builder (`auto/build/*`) | live |
| PR judged | `pr-triage` (labels `triage:{solid,dup,scaffold,stale}`, 6h cadence) | live |
| Solid → merged | `auto-merge` (squash on `triage:solid`, freeze-guard on scoring paths) | live |
| **Red-from-base → re-tested after main recovers** | **`pr-relander`** (update-branch on stale-but-clean PRs on every main push + 6h sweep, capped) | **built 2026-08-29** |
| Hand PR, wanted → merged | `land-when-green` label → `auto-merge` opt-in job + sweep | **built 2026-08-29** |
| Conflicting / twice-relanded-still-red / dup / scaffold → closed | operator, via the triage digest | manual by design |

Rules, binding on every session:

1. **A green, mergeable PR you own does not wait for you to remember it.** On
   a PR this project's flow lets you merge (CLAUDE.md pre-approves
   squash-merging your own), merge it when gates pass; otherwise apply
   `land-when-green` so the machinery owns the wait. "I'll merge it later" is
   a GC-9 decision-parked-without-a-meter.
2. **Red inherited from base is never the PR's verdict.** Before judging a
   red PR, ask whether the same check is red on `main` (§2, interrogative 2's
   silent-state discipline applied to CI). The relander automates the retest;
   sessions must not label or close on an inherited red.
3. **The backlog number is a register metric.** Open-PR count and
   oldest-open-age get re-measured at governance sessions; a rising count
   with green throughput means a link broke — find which link, not which PR.
4. **Closing is honest work.** A dup, a scaffold, or a build superseded by a
   better emission is *finished* by closing with a reason, and that is a
   grade-move, not a failure. The graveyard forms from PRs nobody would
   defend but nobody closed.
5. **Never force the chain.** No merging over red required checks, no
   stripping the freeze-hold, no closing another lane's PR to tidy a number.
   The chain's gates are the product's honesty (§5, S0).

*Register addendum, seeded with the sections above:*

| ID | Gap | Class | Grade | Cap | Evidence trail | Last touched |
|---|---|---|---|---|---|---|
| GR-11 | Graphify graph absent in fresh clones — structural instrument errors until regenerated; no staleness budget on graph-derived claims | GC-1, GC-8 | **C0** | C4 (CI-rebuilt graph per F4 + build-time stamped claims) | `graph_stats` error measured 2026-08-29; `.gitignore:20`, `.graphifyignore` | 2026-08-29 |
| GR-12 | Chairman state has no bus mirror — host-side daemons cannot read governance state | GC-4 | **C0** | C2 (`mesh_memory` write-through per §11.2) | §11.2; `CLAUDE.md` mesh_memory reference | 2026-08-29 |
| GR-13 | Open-PR graveyard: stale-from-base PRs never re-tested after main recovers; hand PRs have no landing path | GC-13, GC-7 | **C2** — mechanism built (`pr-relander.yml`, `land-when-green` in `auto-merge.yml`); C3 needs the backlog observed draining, C4 needs link-ownership asserted by a test | C4 | `tools/pr_triage.py:179` (recorded failure); §13 chain table; 117 open PRs in MERGE_AUDIT | 2026-08-29 |

— the chairman's locum, seeded 2026-08-29
