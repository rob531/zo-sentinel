# RUN 2 / Deliverable A — census of `_tools/`

Measured 2026-09-02, tower-side (Windows). Read-only on every repo; no commits, no
pushes, no PRs, no edits to `_tools`. Nothing that rents a GPU or spends money was run.

- Population censused: `D:\zo\Zocomputer Agents\_tools` — **472 executable files**
  (414 `.py`, 43 `.ps1`, 15 `.cmd`), `__pycache__` excluded. The directory holds 736
  files in total; the other 264 are `.txt/.out/.err/.log/.json/.md/.bak` artifacts,
  not tools.
- Caller corpora, all four surfaces, each read once:
  36 lane `SKILL.md` · 503 `_tools` code files · 113 agent docs at
  `D:\zo\Zocomputer Agents\*.md|*.json` · 10,351 repo files at
  `D:\zo\_ultraplan_run\zo-sentinel` · 19 CI files under `.github/`.
  (154 `_tools` log files were read separately as **execution evidence**, never as callers.)
- Instruments written for this run, kept for audit:
  `D:\zo\_ultraplan_run\tools_census.py`, `recls2.py`, `spot2.py`, `gap.py`,
  `sb200k.py`; raw outputs `census_run.out`, `A.out`, `B.out`, `C.out`,
  `tools_census.json`, `buckets.json`.

---

## 1. `dark_tools.py` — where it is right, where it is wrong

### 1.1 It does not answer this question at all. Its population is a different one.

`_tools/dark_tools.py:157 scan()` builds its population from
`git ls-tree -r --name-only origin/main` at `REPO = D:\zo\zo-sentinel\zo-sentinel`,
filtered to `tools/` + `.py`, minus `test_*` and `__init__.py`.

Ran it tower-side (`--verbose --no-detach`, inside my own detached wrapper so the
~55–90 s transport cut could not orphan it; confirmed by artifact
`D:\zo\_ultraplan_run\dark_run.rc`, not by exit code):

```
BASIS: 99 tools/*.py (tests excluded) against 5150 repo files + 36 lane prompts + 511 agent docs
!! DARK AND UNEXPLAINED: 6
```

**Its denominator is 99 repo `tools/*.py`. The population of this deliverable is 472
files in `_tools/`. The overlap is zero.** `_tools/` appears in `dark_tools.py` only
as *surface 3*, a caller corpus (`(AGENTS / "_tools").glob("*.py")`), and even there
the result is discarded: `scan()` sets `"consulted": bool(r_repo or r_lane)`, so
`agent_refs` is computed, printed, and never counted. Nothing in this fleet has ever
censused `_tools/`. That is the gap this deliverable fills — it **supplements**
`dark_tools.py`, it does not replace it.

### 1.2 Its 6 "dark" verdicts are CORRECT — verified 6 of 6, not the 5 asked for

For each of the 6, every non-self reference to the basename in the clean clone
(`git grep -F <basename>` across `*.py *.yml *.yaml *.json *.sh *.md *.toml *.cfg`):

| tool | non-self hits | what they are |
|---|---|---|
| `tools/feature_completeness_report.py` | 7 | all prose in `_ultraplan_pack/*.md` |
| `tools/graph_domain_digest.py` | 10 | prose, plus `tests/test_graph_domain_digest.py` (tests deliberately excluded) |
| `tools/bakeoff/build_via_rung.py` | 3 | prose + `tools/bakeoff/README.md` |
| `tools/promote_graph_directives.py` | 5 | all prose |
| `tools/ingest_observed_edges.py` | 7 | all prose |
| `tools/check_no_hardcoded_localhost.py` | 2 | all prose |

No executable caller for any of them. **Verdicts sound.**

### 1.3 DEFECT — a repo status doc counts as a caller. True dark is 10, not 6.

`consulted` is true if a `.md` in the repo carries an invocation-*shaped* line. For lane
`SKILL.md` that is correct — the prompt **is** the wiring. For `docs/STATUS_*.md` it is
not: nothing subscribes to a status document.

Four rows the tool calls "consulted" have **zero** code callers on `origin/main`
(`git grep -F <base> origin/main -- '*.py' '*.yml' '*.yaml' '*.sh' '*.json' '*.toml'`
returns 0 non-self hits for each):

| tool | its only "caller" |
|---|---|
| `tools/autopoiesis_bar_tracker.py` | `docs/STATUS_2026-08-28.md` |
| `tools/axis_scorer_grounded.py` | `chairman/QUEUE.md`, `docs/AXIS_SENSE_CHECK_2026-08-28.md` |
| `tools/axis_sense_check.py` | `chairman/QUEUE.md`, `docs/AXIS_SENSE_CHECK_2026-08-28.md` |
| `tools/fu/fu_seed_predicates.py` | `LOCO_CHAIRMAN.md`, `chairman/QUEUE.md`, `docs/MERGE_AUDIT_2026-08-23.md`, `docs/STATUS_2026-08-28.md` |

**Dark-and-unexplained is 10 of 99, not 6.** A 40 % undercount. This is the tool's own
"A MENTION IS NOT A CALL" doctrine, one layer down: it fixed prose *about* a tool and
left documented *commands* in status docs counting as wiring. Fix: keep `.md` as a
caller surface for `SCHED` only, and require a code extension for the repo surface.

### 1.4 DEFECT — `peer_decisions.json` is not a caller surface, and it is one

`_tools/peer_review.py:481` runs stored decision commands verbatim:
`subprocess.Popen(cmd, shell=True, ...)`, fed from `apply/evidence/verify/revert_cmd`
in `D:\zo\Zocomputer Agents\peer_decisions.json` (41 rows). That file is a **caller**.
`dark_tools.py` reads it only into `agent_refs`, which never reaches `consulted`, so any
repo tool wired solely through a peer decision reads as dark. No instance among today's
6 — direction of error is conservative — so this is **latent, not active**.
COULD_NOT_DETERMINE whether it has ever misfired historically; re-running the census with
`peerdec` promoted to a counting surface against each day's `dark_tools.json` would settle it.

### 1.5 LATENT — merging the ultraplan pack would flip 3 dark tools green

`_ultraplan_pack/15_no_status.md:393` contains the literal string
`dark_tools.py --assert-wired tools/feature_completeness_report.py`. That is
invocation-shaped, and `.md` is in the repo caller corpus. It is on branch
`ultraplan/pack-20260902` (HEAD `0e92b5b3`), **not** on `origin/main` (`b591062f`), so
there is no contamination today — verified by `git rev-parse HEAD origin/main`. If that
branch merges, `feature_completeness_report.py`, `graph_domain_digest.py` and
`ingest_observed_edges.py` stop being reported dark on the strength of prose quoting a
command. Same family as the `lane_halt` false positive the tool was built to prevent.

### 1.6 Minor

Surface 3 globs `(AGENTS/"_tools").glob("*.py")` — root only, missing the 14 files under
`_probes/`, `_stage/`, `_staging/`. Harmless while `agent_refs` is uncounted.

### 1.7 Verdict

**Trustworthy for what it measures (repo `tools/*.py` dark side): yes — 6/6 confirmed.
Trustworthy as a fleet adoption number: no — it undercounts dark by 4 (40 %) because it
treats a repo status doc as a caller. Applicable to `_tools/`: not at all.**

---

## 2. Bucket counts

A **caller** here is: an executable file (`.py .ps1 .cmd .bat .sh .yml .yaml`), a lane
`SKILL.md`, or `peer_decisions.json`. A `.md` doc, the FOLLOWUPS ledger, and census
output files (`dark_tools.json`, `_probe_dark_scratch.json`) are **not** callers.

| bucket | count | share |
|---|---:|---:|
| **WIRED** — has a real caller | **122** | 25.8 % |
| **(a) FINISHED ONE-SHOT** — dated / FU-keyed / cycle-keyed / incident script that correctly ended | **235** | 49.8 % |
| **(b) PROBE KEPT AS EVIDENCE** — `probe_*`, `control_*`, `test_*`, `_probes/` | **107** | 22.7 % |
| **(c) CURE BUILT AND NEVER WIRED** | **8** | 1.7 % |
| total | **472** | |

`122 + 235 + 107 + 8 = 472`.

The 19 residual candidates left by the automatic classifier were each read and
adjudicated by hand; 10 went to (a), 2 to (b), 7 stayed in (c), and
`pr_block_census.py` was pulled **out** of (b) into (c) (a name regex containing
"census" had captured it).

### 2.1 Two defects in MY census, found by spot-check and fixed before publishing

Both were caught by sampling 6 "wired" rows and grepping the citing file rather than
trusting the citation.

1. **Prose carried as an argument string is still prose.** The ledger-writer one-shots
   (`_fu_triage_write3_20260802.py`, `append_fu103.py`, `_fu260_write_20260805.py`, …)
   embed whole FOLLOWUPS.md paragraphs — including quoted command lines — as ordinary
   Python string literals. Those survived docstring-stripping. **3 of 6 sampled "WIRED"
   rows were false positives on that basis.** Fixed by blanking string tokens longer
   than 200 chars or containing markdown markers; short strings (real `subprocess` argv)
   are kept. First run said 245 wired; that number was wrong and is not reported here.
2. **`peer_decisions.json` was excluded and is a caller** (§1.4). Adding it moved 16
   files from unwired to wired, including `lh_coverage_adopt.py`,
   `fire_ceiling_ratchet.py`, `fu_ledger_family_evidence.py`,
   `fu_ledger_family_revert.py` and `authority_revert.py` — five tools that would
   otherwise have been published as findings.

**Known remaining limitation.** Line numbers in `tools_census.json` citations drift:
`strip_py_prose()` re-emits a multi-line string token on one line, adding physical lines.
File-level membership — the only thing the buckets depend on — is unaffected; the line
pointers are indicative only. `spot2.py` resolves citations by grepping the original file
for this reason.

### 2.2 A bucket the three-way scheme does not have

`lane_kill.py` (8,570 B) has no caller and is not a finding: it is the **approval-free
kill switch**, invoked by a human at a prompt by design, and is named as such in
`MEMORY.md`. `dark_tools.py` handles this with `EXPECTED_DARK` ("manual break-glass,
invoked by a human by design"). Filed under (a) for arithmetic, but a standing
break-glass tool is neither finished nor a probe — R6 wants a fourth bucket,
**(d) STANDING, HUMAN-INVOKED BY DESIGN**, or every future census will re-litigate it.

---

## 3. Bucket (c) — the finding. 8 cures built and wired to nothing.

Every one was run. Six work; **two are wrong**, which is the standing finding
("a dark tool is by definition an untested tool") landing again.

### (c1) `peer_state_consistency.py` — 12,320 B — WORKS
FU-344 detector: does a peer-review row's `state` agree with the `acted`/`reverted`
blocks inside it? `--self-test` PASS over 8 cases, **observing both RED and GREEN**, so a
green means something. Live read-only run: 41 rows, `ACTED=13 COMPLETE=2 FALSIFIED=23
REVERTED=3`, 0 `REVERT_FAILED`, **VERDICT GREEN**, plus 2 advisory `STALE_REVERT_BLOCK`
rows — one of which is `bar-csv-machine-writer-must-not-erase-graded-rows`, superseded
`2026-09-02T07:31:02Z`, an item `MEMORY.md` still carries as open.
**To wire:** one line in `daily-chairman-review` or `follow-up-triage`. It already has
`--file` and a 0/1 rc contract. Cost: one line.

### (c2) `tower_path_doors.py` — 8,352 B — WORKS, AND IS RED RIGHT NOW
FU-343 census: every `_tools` door that takes a lane-supplied path must consult
`friction.tower_invisible()` before publishing an ABSENT verdict. `--self-test` PASS over
5 cases including the 2026-08-13 false-positive case, both RED and GREEN observed.
`--check` today:

```
doors that read a LANE-SUPPLIED path: 10   (excluding the classifier itself)
  consult friction.tower_invisible : 6
  DO NOT                           : 4
UNGUARDED DOORS
    fu_verify.py              --negative-control
    migration_content_class.py --file
    record_prod_fire.py       --count-attempts
    scheduler_mirror.py       --path
VERDICT: RED
```

A correct instrument, reporting a live red, that no one reads. This is
`a_cure_wired_into_one_door_of_eight_reads_as_a_cure` measured from the other end.
**To wire:** one `--check` line in a lane prompt; rc contract already 0/1/2. Cost: one line.

### (c3) `claude_paths.py` — 9,303 B — WORKS
Resolves the MSIX roaming-vs-container `%APPDATA%` split, where one path string denotes
two real locations and the wrong one returns empty with **no error**. `--selftest`:
`SELFTEST OK -- 9 controls, incl. the missing-path negative control`. `--scan` over
`_tools` today finds **6 files hardcoding the roaming view** — `friction.py` (213 KB, the
most-imported library in the fleet), `kl_link_audit.py`, `memdiag.py`, `memdiag2.py`,
`probe_lane_path_doors_20260813.py`, `probe_scratchpad_classify_20260813.py`.
Its own docstring states a **rule for every lane** ("any tower-local tool needing app-side
state must resolve through `claude_paths.resolve()`"), and nothing imports it — its only
references anywhere are prose inside `closeout5_20260801.py`. A rule with no subscriber.
**To wire:** a standing `--scan` line in a lane prompt (cheap, non-invasive), and/or
`resolve()` in the 6 flagged files. Cost: one line for the check.

### (c4) `pr_net_deletions.py` — 8,950 B — WORKS
FU-170 gate: reports symbols a PR deletes from `main` that its own body never mentions —
built after PR #2293 silently deleted `_salvage_transcript` past 15 green checks.
`--selftest`: 3/3 PASS with a **positive** control (`150daaf2..e2744d3` deletes
`_salvage_transcript`), a **negative** control (identical inputs report nothing), and a
**repair** control (`a59ab384` restores it).
**To wire:** a step in the PR-gate workflow, or a line in the merge lane. Needs `gh` +
`GH_TOKEN`, which it already self-fetches via `D:\agentvault\fetch_secret.py`.
Caution: adding a *required* context to branch protection while 239 PRs are already
dammed would deepen the dam — wire it advisory-first.

### (c5) `pr_regate.py` — 5,633 B — WORKS in `--dry-run`; NOT run live
Recovery sweep for PRs whose required contexts **never ran**: close + reopen emits a
`reopened` event that creates the runs. Cohort discovered, never hardcoded.
`--dry-run --limit 300` (read-only):

```
BASIS: 285 open PRs, 244 BLOCKED/UNKNOWN to inspect
BASIS: 160 refused by the negative control (already carry >=1 required context)
--dry-run: nothing changed.
```

The negative control fires 160 times, so it discriminates rather than merely passing.
244 − 160 = **84 PRs it would close and reopen**.
**I did not run it live.** That mutates 84 pull requests on GitHub; it needs explicit
human approval, not an agent's judgement. It is the direct remedy for the 108 never-ran
PRs (c6) measures.
**To wire:** approval first, then a line in the PR-hygiene lane.

### (c6) `pr_block_census.py` — 3,604 B — **WRONG**. Prints 3 cells of a 4-cell partition.
Live read-only run: 285 open PRs, **239 BLOCKED**. It then reports:

```
BLOCKED purely because a required context NEVER RAN: 108
BLOCKED with a genuinely RED required context:        86
BLOCKED with all required green (=> review/other):    0
```

`108 + 86 + 0 = 194 ≠ 239`. Source (`pr_block_census.py:75-77`) computes
`only_missing = missing and not failing`, `only_failing = failing and not missing`,
`neither = not missing and not failing` — and **never computes or prints `both`**.
Re-parsing the tool's own per-PR table (`gap.py`) gives the missing cell exactly:

```
only MISSING 108 | only FAILING 86 | BOTH (UNREPORTED) 45 | neither 0 | sum 239
```

**45 blocked PRs — 19 % of the population — appear in no summary line.** The headline
"86 with a genuinely RED context" understates the red population by 34 %: the true figure
is **131**. The basis lines and both histograms are correct; only the partition is
broken. A birth defect, never caught because nothing runs it.
**To wire:** add `both` and print it (3 lines), *then* wire. Wiring it as-is publishes a
number that is wrong by 34 %.

### (c7) `pr_red_triage.py` — 3,845 B — **BROKEN**. 13 of 13 samples UNCLASSIFIED.
Its purpose is to settle FU-256 — is a red required check genuinely red, or
**red-and-empty** (0 tests collected)? "Merge on the COUNT, never the colour" depends on
this distinction. Live read-only run, verdict histogram: `{'UNCLASSIFIED': 13}`. Every
single one failed the same way:

```
#2981   pytest -> UNCLASSIFIED
        | {"message":"Not Found", ... "download-job-logs-for-a-workflow-run", "status":"404"}
```

It classifies by downloading the failing job's log tail; the logs are gone (GitHub
retention) or the check-run→job mapping no longer resolves. **The tool has exactly one
outcome in practice and cannot answer the question it exists for.** Nobody knew, because
nothing calls it. This is the cleanest instance of the standing finding in this census.
**To wire:** it needs repair before wiring — classify from the check-run *annotations* or
the pytest summary artifact rather than expired job logs. COULD_NOT_DETERMINE whether it
ever worked; re-running it against a PR whose logs are inside the retention window (a
check run from the last ~90 days) would settle that, and is the first thing to try.

### (c8) `plan_200k_log_upsert.py` — 1,766 B — WORKS, but **HAS ROTTED**
Idempotent upsert of one dated row into `plan_200k_count_log.csv`. Run against a sandbox
reproducing its exact path layout (`<sb>/_tools/<tool>` + `<sb>/plan_200k_count_log.csv`)
so the real code path executes and the live file is never touched — live CSV 8,423 B
before and after, `filecmp` identical.

```
rc = 0   stdout: OK 36 rows; upserted 2026-09-02
NEGATIVE CONTROL rc = 1  AssertionError: identity broken: 498694-296109 != 999999
```

The negative control goes red correctly. **But its schema is stale.** `HDR` is 6 columns;
the live CSV has **7** — `scores_rows` was added after the tool was written on 2026-08-02.
Proven by diffing headers after the sandbox run:

```
sandbox after tool:  date,registry_rows,scored_servers,never_scored,trusted_servers,note
live (untouched):    date,registry_rows,scored_servers,never_scored,trusted_servers,note,scores_rows
```

`w.writerow(HDR)` rewrites the header with 6 names, so **running it today silently drops
`scores_rows` from the header** — every `csv.DictReader` consumer loses the invariant
column — and every row it writes has an empty `scores_rows`. Existing rows keep their 7th
field (the code pads short rows but never truncates long ones), so the file becomes
header-inconsistent rather than obviously broken. Its own three assertions do not check
column count, so this passes green.
The lane is already maintaining the CSV by some other path (a `2026-09-02` row exists,
registry 500,945), which is why nothing noticed.
**A cure that has rotted while dark is worse than no cure: this one is armed to corrupt
the log it was built to protect.** **To wire:** add `scores_rows` to `HDR` and a 7th
positional argument, add a column-count assertion, *then* wire.

### (c) summary

| # | tool | bytes | ran? | verdict |
|---|---|---:|---|---|
| c1 | `peer_state_consistency.py` | 12,320 | yes | WORKS — green, 2 advisories |
| c2 | `tower_path_doors.py` | 8,352 | yes | WORKS — **RED now, 4 unguarded doors** |
| c3 | `claude_paths.py` | 9,303 | yes | WORKS — **6 files flagged, incl. `friction.py`** |
| c4 | `pr_net_deletions.py` | 8,950 | yes | WORKS — 3/3 controls |
| c5 | `pr_regate.py` | 5,633 | dry-run only | WORKS — would touch 84 PRs; **needs human approval** |
| c6 | `pr_block_census.py` | 3,604 | yes | **WRONG** — 45 of 239 PRs unreported; red count understated 34 % |
| c7 | `pr_red_triage.py` | 3,845 | yes | **BROKEN** — 13/13 UNCLASSIFIED, 404 on job logs |
| c8 | `plan_200k_log_upsert.py` | 1,766 | sandboxed | WORKS but **ROTTED** — drops the `scores_rows` column |

Five of the eight (c4–c8) are the **PR-dam and count-integrity family**. That is not a
coincidence: the fleet built its PR-unjamming toolkit, ran each piece by hand once, and
wired none of it — while the dam grew to 239 blocked PRs.

---

## 4. Serving R6 (fleet hygiene)

### 4.1 The honest denominator for "tool adoption"

**Not 472.** 342 of the 472 files (72.5 %) are spent probes and correctly-finished
one-shots. They *should* have no caller; counting them as unadopted manufactures a 26 %
adoption figure that measures the fleet's history, not its hygiene.

```
adoption denominator = WIRED + (c)          = 122 + 8  = 130
adoption numerator   = WIRED                = 122
TOOL ADOPTION = 122 / 130 = 93.8 %
```

Excluded from the denominator, with reasons on record:
- **(a) 235** finished one-shots — a dated / FU-keyed / cycle-keyed script that ended
  correctly. Uncalled is success.
- **(b) 107** probes and controls retained as evidence. Uncalled is the point.

The naive number is `122 / 472 = 25.8 %`. The gap between 25.8 % and 93.8 % is the whole
of R6's complaint: the fleet has no honest denominator, so any adoption metric computed
over `_tools/` today is a number about archaeology.

**Recommendation.** Give `_tools/` the mechanism `dark_tools.py` already has for the repo:
an `EXPECTED_DARK`-equivalent, keyed on full relative path, each entry carrying a reason.
Seed it with the (a) and (b) classifications in `D:\zo\_ultraplan_run\buckets.json`.
Then the denominator is derived, not asserted, and a new uncalled tool surfaces the day
it is written instead of joining a pile of 342.

### 4.2 Orphan archival

| class | count | action |
|---|---:|---|
| (a) finished one-shots | 235 | archive to `_tools/_archive/oneshot/`, keep in place readable |
| (b) probes kept as evidence | 107 | archive to `_tools/_archive/probes/` |
| non-tool artifacts (`.txt/.out/.err/.log/.bak`) | 264 | archive or prune; they are 36 % of the directory |
| **archival candidates total** | **606 of 736 files** | |
| stays live | 130 tools (122 wired + 8 to wire or fix) | |

Two constraints on the archival, from evidence in this run:
1. **Archive, do not delete.** `_tools` is **not a git repo**
   (`git rev-parse --is-inside-work-tree` → rc 128, recorded in `authority_revert.py`),
   so a delete has no undo. The 154 log files are the only execution evidence that
   distinguishes "one-shot that ran and finished" from "cure that never ran" — they are
   what let this census bucket several files.
2. **Move `_probes/`, `_stage/`, `_staging/` last.** `dark_tools.py` globs only
   `_tools/*.py` at root, so anything moved into a subdirectory leaves its caller-surface
   corpus. Fix §1.6 (`rglob`) before relocating, or the next repo census loses 342 files
   of caller text and its dark count will jump for no real reason.

### 4.3 Three fixes that are cheap and unblock the rest

1. `dark_tools.py`: require a code extension on the **repo** caller surface (keep `.md`
   for `SCHED`). Corrects 6 → 10. ~2 lines.
2. `dark_tools.py`: count `peer_decisions.json` toward `consulted`. Prevents a
   peer-wired tool reading dark. ~3 lines.
3. `pr_block_census.py`: compute and print the `both` cell. Corrects a published
   number that is wrong by 34 %. ~3 lines.

---

## 5. What could not be determined

- **Whether `pr_red_triage.py` has ever worked.** It fails 13/13 on expired job logs.
  Settle it by running it against a check run inside GitHub's ~90-day log retention.
- **Whether `dark_tools.py`'s missing `peerdec` surface has ever caused a false dark.**
  No instance today. Settle it by re-scoring the archived daily `dark_tools.json` files
  with `peer_decisions.json` promoted to a counting surface.
- **Whether the 84 PRs `pr_regate.py --dry-run` selected would actually regate.** Only a
  live run answers that, and a live run mutates 84 PRs. Not attempted; needs approval.
- **Dynamic invocation by constructed path.** A tool launched via a variable
  (`T + r"\x.py"`) is caught only when the literal basename appears on the line. Any tool
  invoked through a fully computed name is invisible to this census and would land in
  (a)/(b)/(c) wrongly. Settle it by instrumenting `friction.detached()` /
  `self_detach()` to log the resolved child argv, then folding those logs in as a
  caller surface — that would make adoption *observed* rather than *inferred*.
- Bucket (a)/(b) assignments for the 332 files not individually read were made by name
  pattern (date / FU id / cycle id / incident prefix / probe prefix). The 19 residual
  ambiguous files were each read. A misfiled (a) or (b) would not change the (c) list,
  which is what this deliverable turns on.
