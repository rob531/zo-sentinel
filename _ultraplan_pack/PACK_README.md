# `_ultraplan` — the ultra plan pack for zo-sentinel / mcprisky.io

Built 2026-09-02 for a run on 2026-09-05/06. Three ultra plans available; two are
scoped here, one held in reserve.

## What is here

```
build_pack.py              the builder — re-run it, don't edit the pack by hand
registered_tasks.json      scheduler roster snapshot; refresh before the run
RUN1_LEDGER_RECKONING.md   entry prompt for run 1
RUN2_REPO_AUDIT.md         entry prompt for run 2 (consumes run 1's output)
pack/                      generated. Everything in here is disposable and
                           reproducible; nothing in here is hand-written.
```

## Rebuild before the run — this is not optional

```
python "D:\zo\Zocomputer Agents\_ultraplan\build_pack.py"
```

Takes about 40 seconds. Every count carries a `measured_at`; the manifest carries
a staleness rule. A pack built on the 2nd and read on the 6th describes a system
four days and a dozen ledger entries in the past — and this project already holds
three separate findings about censuses that measured a world which had moved.

Also refresh `registered_tasks.json` (ask Claude to list scheduled tasks, rewrite
the file). If it is more than 7 days old the builder **refuses to label lanes from
it** and every lane reads `COULD_NOT_DETERMINE` rather than being guessed.

`--no-live` skips network probes. `--self-test` runs the 30 parser controls and
writes nothing.

The builder never opens `FOLLOWUPS.md` for writing, and refuses any output path
resolving to a `FOLLOWUPS*` name or escaping `pack/`.

## What assembling this turned up

This was supposed to be packaging. It found four real defects — which is the
argument for the exercise, and worth reading before the run.

**43 entries carry no `status:` field at all.** Neither open nor closed. The
triage lane is the only writer of status lines and sweeps on that key, so these
are invisible to it: unworkable, uncloseable, absent from every open-count. They
cluster from 2026-08-10, when several lanes moved to a prose-heavy entry shape.
Two early drafts of this pack dropped them entirely by selecting on `open` —
which is exactly how they became invisible in the first place. The selection rule
is now `open OR undetermined`, and they have their own artifact.

**The ledger uses five metadata separators**, including `|` and a mojibake pair
left by a lane that round-tripped the file through latin-1. A naive parse read
109 of 375 entries as statusless. The `|` miss alone misfiled 7 entries — among
them **FU-361**, the frozen-corpus-floor hazard your own `MEMORY.md` carries by
number, which was consequently dropped from Tier 1 as unprioritised. The builder
now aborts if any entry contains a status token it could not read, so this class
fails loudly instead of silently reclassifying.

**36 lane directories exist on disk; 21 are registered and 18 enabled.** The 16
orphans are finished one-shots and abandoned watches — `score-wave-check-2`,
`fu104-monitor-run-20260726-014732`, `campaign-wave-…-landing-check` — whose
`SKILL.md` still reads like a live prompt, several carrying standing instructions
and away-window dates. Reading the directory count as the fleet overstates it by
60%; reading an orphan's prompt as current doctrine is worse.

**The PR queue is 276, not 200.** The first probe asked `gh` for a limit of 200
and got exactly 200 back — a cap reported as a count. Two probes minutes apart
also returned 4 and then 22 `CONFLICTING` against the same queue, because GitHub
computes mergeability lazily. The builder now refuses a list returned at its own
limit, and publishes the full histogram rather than binning `UNKNOWN` as healthy.

## Measured at build time (2026-09-02T19:53Z)

| | |
|---|---|
| ledger entries | 375 (max id FU-382, 7 id gaps) |
| open | 168 — 54 at P0/P1 |
| undetermined (no status field) | 43 |
| closed | 164 |
| **actionable** | **211** — 59 at P0/P1, all verbatim in Tier 1 |
| stale >30d | 93 of the open set |
| open, never logged | 32 |
| open with no verify predicate | 12 |
| registry rows | 500,945 — **250% of the PLAN_200K row goal** |
| scored servers | 296,109 — **59.1% coverage** |
| newest score | 2 days old |
| oldest score | **70 days, and not moving** |
| open PRs | 276 — 253 mergeable, 22 conflicting, 1 unknown; oldest 2026-07-19 |
| lanes | 36 dirs, 21 registered, 18 enabled, 16 orphaned |

The row goal was met in July. The live question is defensibility, not count —
which is what PLAN_200K's own definition of "assessed" always said.

## Pack contents and cost

~601K tokens. The reading order in `RUN1_LEDGER_RECKONING.md` is built so the run
never has to hold all of it: stats and prod state first, hazards before any
proposal, full text only for what is actionable.

| file | ~tokens | what it is |
|---|---|---|
| `31_ledger_stats.json` | 5K | the shape of the problem in one screen |
| `30_prod_state.json` | 16K | live git / PR / freshness, every field stamped |
| `22_hazards.md` | 91K | 531 memory files — moves already known to bite |
| `12_themes.md` | 23K | where actionable work clusters, by hazard theme |
| `15_no_status.md` | 88K | the 43 undetermined, full text |
| `10_open_p0p1_full.md` | 162K | 59 actionable P0/P1, verbatim |
| `14_open_p2p3_digest.md` | 19K | open below P1, one paragraph each |
| `13_closed_index.jsonl` | 19K | closed entries + resolution evidence |
| `20_lanes.md` | 30K | 36 lane dirs, registration status, prompt heads |
| `21_goals.md` | 19K | PLAN_200K, GOVERNANCE, recent AUTOPOIESIS |
| `11_all_fus.jsonl` | 131K | one row per entry — compute over it, don't read it |

## Design notes

**Themes, not families.** Connected components over the `[[FU-nnn]]` citation
graph were computed first and discarded: the largest held 265 of 375 entries. A
family swallowing 70% of the ledger is a label hiding its call sites. The themes
used instead are the taxonomy from `MEMORY.md`, so a plan reading this pack and a
plan reading the standing hazard index sort the world the same way.

**Unknown is never zero.** Any field the builder could not measure is emitted as
`{"state": "COULD_NOT_DETERMINE", "reason": …}`. Nothing is carried from a
previous build. An empty hazard file would read exactly like an absence of
hazards, so the memory directory is discovered rather than hardcoded and says so
loudly when it cannot be found.

**Two controls run before any artifact is written.** The first proves no entry
was dropped (heading count == parsed count). The second — the one that matters —
proves no entry was silently *reclassified*: any block containing a status token
the field parser failed to read aborts the build by name. The first control was
green through three builds while the `|` defect was live; only the second can see
it.

**Names match what they count.** An earlier manifest published `open_p0p1: 54`
against a file holding 58 — two quantities under near-identical names. Tier 1's
count is now `actionable_p0p1_in_tier1`, and `open_only_p0p1` is published
separately.

**The self-test carries a case per shape**, with negative controls, because a
check that cannot fail proves nothing. 30 cases. The two regression guards for
the `|` separator and the `opened:` alias are labelled as such: both shipped
broken and were caught by an adversarial read of the output, not by the suite —
which is the honest reason they are in it now.
