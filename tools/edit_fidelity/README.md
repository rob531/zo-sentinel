# edit_fidelity -- does a repairer edit more than the bug?

An implementation of the measurement in arXiv:2609.04061, *"When Models Edit
Too Much: On the Fidelity of Minimal Code Edits"*, run against **this
repository's own modules and its own pytest targets** rather than a public
benchmark. It exists because the builder's output is graded on green CI, and
green CI cannot see the difference between a one-token fix and a rewrite of the
enclosing function that happens to still pass.

## What it measures

A **task** is `(source_file, test_target, corruptions)`. `corrupt.py` injects
seeded, AST-located defects into a live repo file, each with a **known
inverse** (`comparison_flip`, `boundary_shift`, `boolop_flip`, `arg_swap`,
`guard_drop`, `unary_not_drop`). Every splice preserves every other byte of the
file, so the clean file is a legitimate reference: it is exactly what a
minimal repair should reproduce.

A repairer is handed the corrupted file, and its output is scored against the
clean original:

| metric | definition | reads as |
|---|---|---|
| `fidelity_lev` | `Levenshtein(reference, output) / len(reference)` | 0.0 = byte-identical to the original; the paper's "excess edit" |
| `delta_cc` | `cognitive_complexity(output) - cognitive_complexity(reference)` | > 0 = the repair made the module harder to read |
| `pass@1` | the task's own pytest target, run as a subprocess | did it still fix the bug |

`cognitive_complexity` implements a documented subset of SonarSource's rule
set (see the docstring in `metrics.py`, including the one deliberate
deviation: `with` is scored). It is not the reference implementation and does
not claim to be.

### The validity gate is the point

A corruption is a **valid task only if the test target passes on the clean
file and fails on the corrupted file.** Corruptions the suite cannot see are
discarded **and counted**, and the count is printed in every report. It is a
finding about this repo's coverage, not noise to hide: this repo's own scars
include a green that ran zero tests, and 64% of the suite that had never once
run. A clean run that collects zero tests is treated as *not green*.

This gate is what gives the eval a ground truth. Grading a repair against
"CI is green" has none.

### Task shapes: `--shape easy` (default) and `--shape hard`

The same harness, one parameter, **no fork**. The gate above is byte-identical
in both.

| | easy | hard |
|---|---|---|
| corruptions per task | 1 | 2-3, in different functions where possible |
| enclosing function | any | `>= --min-func-lines` (10), longest first |
| file window | 25+ lines, <= 9 KB | 60+ lines, <= 11 KB |
| prompt names the function | yes | **no** |
| prompt names the failing test | yes | **no** |
| prompt carries the pytest tail | yes | **no** |

The hard shape exists because the easy one could not answer its own question:
Sonnet 4.5 reproduced the file byte-for-byte in 34 of 40 calls, so a
preservation instruction had no headroom to improve anything (see the two
measured sections below). Raising difficulty is the only way to find out
whether the instruction does anything -- *if* the plain arm lifts off the floor.

Hard-shape tasks are gated **twice**, and the second gate is strictly stronger
than the easy one, never weaker: each corruption must be red **on its own**,
and the combination must also be red. Without the per-component gate a combo
could be red because of one visible bug while the suite is blind to its
partner, and a repairer that fixed one hunk would go green and be scored as
though it had done the multi-hunk repair.

### The two poles (self-test)

`run_eval.py --self-test` runs two repairers on live repo files and exits
non-zero unless BOTH hold:

* `OracleRepairer` applies the known inverse. Must score `fidelity_lev == 0.0`
  and pass -- the GREEN pole.
* `SloppyRepairer` restores correctness, then rewrites **every** enclosing
  function the task touches, the way an over-editing model does (`ast.unparse`
  reformat, comments lost, locals renamed, an unnecessary re-raising
  `try/except` added). Must pass the tests yet score `fidelity_lev` well above
  0 and `delta_cc > 0` -- the RED pole. A metric that cannot separate these two
  is not a metric.

The self-test takes `--shape`, and must be re-run under any new shape: poles
that separated on one task shape are not evidence about another. Hard-shape
numbers are meaningless unless the poles separate on hard-shape tasks.

### The A/B

`AnthropicRepairer(preservation=True|False)` is a real model call. The two
arms use prompts that differ **only** by one preservation instruction
(`repairers.PRESERVATION_INSTRUCTION`; the test suite asserts the prompts are
otherwise identical). The call is made over stdlib `urllib`; output tokens,
call count and cumulative estimated cost are all capped, and usage is printed.

## Zero new runtime dependencies -- not negotiable

Everything here is stdlib: `ast`, `difflib`, `json`, `subprocess`, `re`,
`urllib`, `argparse`. Levenshtein is ~20 lines in `metrics.py`; cognitive
complexity is an `ast.NodeVisitor`; the API call is `urllib.request`. The
`anthropic` SDK is deliberately **not** imported even though it is installed
on the tower.

The reason is FU-118: the MCP SDK the bridges import was declared in **no**
requirements file, and the fleet paid for that in a broken import on a clean
host. An eval that quietly dragged in `python-Levenshtein` or the SDK would be
the same scar again. `tests/test_edit_fidelity.py::test_harness_imports_only_stdlib`
fails the PR if anyone adds one.

## Running

```
# prove both poles on live repo files (exit 2 if they do not separate)
python tools/edit_fidelity/run_eval.py --self-test

# discover >= 20 valid tasks from the repo's own evaluator.yml pytest list, run all four arms
python tools/edit_fidelity/run_eval.py --n-tasks 20 \
    --arms oracle,sloppy,anthropic_plain,anthropic_preserve \
    --model <model-id> --key-cmd "python D:/agentvault/fetch_secret.py anthropic" \
    --max-calls 25 --max-output-tokens 4096 --max-cost-usd 2.0

# re-run the model arms on the SAME cached tasks
python tools/edit_fidelity/run_eval.py --tasks-json tools/edit_fidelity/results/tasks_<seed>.json \
    --arms anthropic_plain,anthropic_preserve --model <model-id> --key-cmd ...

# the HARD task shape: 2-3 bugs per task, long enclosing functions, and a prompt
# with the function name, the failing test and the pytest tail all withheld
python tools/edit_fidelity/run_eval.py --shape hard --self-test
python tools/edit_fidelity/run_eval.py --shape hard --n-tasks 30 \
    --arms anthropic_plain,anthropic_preserve --model <model-id> --key-cmd ...

# re-score SPECIFIC rows after fixing an extractor or a metric, without paying
# for the whole A/B again, then merge them into the original run
python tools/edit_fidelity/run_eval.py --shape hard --arms anthropic_plain \
    --tasks-json .../tasks_hard_<date>.json --only-tasks "<id>,<id>" --model ... --key-cmd ...
python tools/edit_fidelity/merge_runs.py --base results_<A>.json \
    --override results_<B>.json --label corrected
```

The key is read from `--key-cmd` stdout or `ANTHROPIC_API_KEY`; it is never
printed, logged or written. The model id is validated against `/v1/models`.

Exit codes: `0` evaluated, `1` error, `2` self-test poles did not separate,
`3` **UNEVALUABLE** (no valid task survived the gate -- never reported as
"0 excess edits"), `4` budget cap reached mid-run (partial results written).

Outputs land in `tools/edit_fidelity/results/`: `tasks_<seed>.json` (the task
set with the gate's discard counts and the full reference/corrupted texts, so
an A/B is reproducible after the files on `main` move), `results_<ts>.json`
(per-task rows with unified diffs) and `report_<ts>.md`.

## Measured, EASY shape (2026-09-12, main @ efd2e8d)

Seed 20260912; evaluator.yml pytest list (57 targets) -> 28 (source, test) pairs
in the 25+ line / <= 6 KB window; 13 pairs walked. Model
`claude-sonnet-4-5-20250929`, temperature 0, whole-file output.

**Validity gate:** 79 candidate corruptions tried -> **20 valid, 59 discarded
because the target stayed green** (0 timeouts, 0 syntax rejects). Three quarters
of the single-token mutations in these well-tested modules were invisible to the
test that covers them; by class, `boundary_shift` was the least visible (1 valid
of 19) and `unary_not_drop` the most (3 of 4).

| arm | n | fidelity_lev mean | median | max | delta_cc mean | pass@1 |
|---|---|---|---|---|---|---|
| oracle (known inverse) | 20 | 0.0000 | 0.0000 | 0.0000 | 0.00 | 20/20 |
| sloppy (rewrite fn) | 20 | 0.0652 | 0.0663 | 0.1287 | +1.00 | 20/20 |
| Sonnet, plain prompt | 20 | 0.0008 | 0.0000 | 0.0165 | 0.00 | 19/20 |
| Sonnet, + preservation instruction | 20 | 0.0016 | 0.0000 | 0.0090 | -0.05 | 20/20 |

Paired (same 20 tasks): preservation arm has LOWER fidelity_lev on 1 task,
HIGHER on 4, tied at exactly 0.0 on 15 (two-sided sign test p = 0.375).
delta_cc: 1 / 0 / 19 ties. pass@1: 20 vs 19.

**Direction vs. the paper -- not reproduced.** The paper reports excess
Levenshtein 0.195 -> 0.131, cognitive complexity -26.6 %, pass@1 +2.3 with the
preservation instruction. Here both model arms sit at the floor (median 0.0000,
15/20 exact ties) and the preservation arm is, if anything, marginally worse on
fidelity (4 vs 1, not significant). Cognitive complexity moved on one task
(-1, in the preservation arm, from `if not isinstance(f, dict) or not f` being
simplified to `if not f` -- a dropped type check the tests cannot see, in the
arm told not to simplify). pass@1 differs by one task, where the plain arm put a
stray filename line inside its code fence and the module failed to import. None
of that is evidence for or against the paper's effect at n = 20 on one repo; it
is evidence that THIS task shape does not exercise it: a single-token mutation,
the failing pytest tail and the enclosing function's name make localisation
trivial, and Sonnet 4.5 at temperature 0 then reproduces the file byte-for-byte
(up to line endings) in 34 of 40 calls. The instrument itself discriminates --
the sloppy pole is 40-80x the model arms on the same metric -- so the null is a
statement about the task difficulty, not about the metric.

Where the model arms did drift, it was never a function rewrite: an
equivalent-but-different fix for `arg_swap` (keep the swapped `select()`
columns, flip the tuple unpacking instead), `if row is None` reconstructed as
`if not row` after a `guard_drop`, and a `# filename` header line added above
the module docstring. `guard_drop` is worth knowing about when reading fidelity:
the dropped condition is under-determined by the tests, so a correct
reconstruction can legitimately differ from the reference.

Spend: this run 40 calls, 84,514 input / 64,713 output tokens, est. **$1.22**.
Two earlier attempts on the same tasks were aborted after 3 and 9 calls (plus one
debug call) while fixing the two artifacts below; at this run's measured
~$0.031/call that is ~$0.40 more, so **~$1.62 total**, under the $2 cap.

Two artifacts found and fixed on the way, both now pinned by tests:

* **CRLF.** The tower checkout has `core.autocrlf=true`; the model answers in
  LF. Before normalising line endings every byte-perfect repair scored
  fidelity 0.022 -- exactly lines / chars -- identically on every task in a
  file. `fidelity_lev` now measures LF-to-LF (`metrics.normalize_eol`).
* **Multiple fences.** Told to return "the file and nothing else", the model
  put a 47-byte diagnostic snippet in a first fence and the file in a second.
  First-fence extraction scored that repair at 0.99; `extract_code` now takes
  the largest fence and records `n_fences` in the row.

Artifacts: `results/tasks_20260912.json` (the 20 tasks with reference and
corrupted text, the pytest tails, and the gate counts),
`results/results_20260912_203127.json` (every row with the raw model output
and a unified diff), `results/report_20260912_203127.md`.

## Measured, HARD shape (2026-09-13, main @ 160ce89)

The easy-shape null above was uninterpretable: both model arms sat on the
floor, so a preservation instruction had nothing to improve. This run raises
difficulty on all three levers at once (`--shape hard`) and asks again.

Seed 20260912; 61 evaluator.yml targets -> 50 (source, test) pairs in the 60+
line / <= 11 KB window; 23 pairs walked. Model `claude-sonnet-4-5-20250929`,
temperature 0, whole-file output.

**Validity gate:** 371 candidates tried -> **107 individually-red components,
264 discarded because the target stayed green**, 103 skipped for sitting in a
function under 10 lines, 0 timeouts, 0 syntax rejects. Those 107 components
grouped into **30 combos, all 30 red -> 30 valid tasks** (21 of 2 nodes, 9 of
3; 25 tasks inside one function, 5 spanning two; mean enclosing function
**36.5 lines**, up from the easy shape's short helpers).

**Self-test poles under the new shape** -- hard-shape numbers would mean
nothing without this:

| pole | fidelity_lev mean | median | max | delta_cc mean | pass@1 |
|---|---|---|---|---|---|
| oracle (known inverse of 2-3 splices) | 0.0000 | 0.0000 | 0.0000 | 0.00 | 30/30 |
| sloppy (rewrites every touched function) | 0.0667 | 0.0660 | 0.1035 | +1.17 | 30/30 |

`separated=True`. The instrument still discriminates on multi-node tasks.

### The A/B

| arm | n | fidelity_lev mean | median | max | delta_cc mean | delta_cc>0 | pass@1 |
|---|---|---|---|---|---|---|---|
| Sonnet, plain prompt | 30 | 0.0025 | 0.0000 | 0.0273 | -0.33 | 0 | 27/30 |
| Sonnet, + preservation | 30 | 0.0021 | 0.0000 | 0.0273 | -0.33 | 0 | 24/30 |

Fidelity split by pass@1 -- over-editing and breaking are different things:

| arm | passed n | fidelity (passed) | byte-perfect (passed) | failed n | fidelity (failed) |
|---|---|---|---|---|---|
| plain | 27 | 0.0015 | 22 | 3 | 0.0112 |
| preservation | 24 | 0.0009 | 20 | 6 | 0.0072 |

Paired on the same 30 tasks: preservation has LOWER fidelity_lev on 4, HIGHER
on 5, **tied on 21** (sign test p = 1.0000). `delta_cc` is tied on all 30 --
neither arm raised complexity on a single task. pass@1 is paired too, so it
gets a paired test: McNemar exact over the 3 discordant tasks (all 3 favour
the plain arm), p = 0.2500.

### The plain arm did NOT lift off the floor

That is the result. Median fidelity is still **exactly 0.0000**; 22 of the 27
successful plain repairs are byte-identical to the original; the worst repair
in either arm is 0.0273, still **2.4x below the sloppy pole's best** and ~25x
below its mean. Tripling the mean (0.0008 easy -> 0.0025 hard) moved it from
"indistinguishable from zero" to "almost indistinguishable from zero".

So the precondition for the whole question failed again, and the honest
conclusion is the one this shape was built to be able to state: **this
repository cannot exhibit over-editing at this task shape either.** Sonnet 4.5
at temperature 0, given 2-3 bugs, no failing-test name, no function name and
no pytest tail, in functions averaging 36.5 lines, still returns the file
byte-for-byte. Not "the preservation instruction does not work" -- there is
nothing here for it to work on.

**Could this sample have seen the paper's effect?** No. Only **9 of 30** pairs
are non-tied, and with 9 non-tied pairs the two-sided sign test has power
0.12 / 0.30 / 0.60 against a per-pair preference of 0.65 / 0.75 / 0.85; the
smallest preference detectable at 80% power is **0.91**. This sample could
only have detected a near-deterministic effect. The null is "n too small to
tell", stated rather than implied -- and the reason n is effectively 9 is
itself the finding: 21 pairs are tied because both arms are byte-perfect.

### The instrument bug this run found, and what it cost

The first scoring of this A/B reported plain fidelity **0.3058** and pass@1
17/30 against preservation 0.0632 and 22/30 -- a large apparent win for the
preservation instruction, in the paper's direction. **It was entirely an
artifact of this harness**, and the split-by-pass table is what exposed it:
the "lift" lived only in failed rows (plain 0.7027) while passing rows stayed
on the floor (0.0024).

Under the hard prompt the model answers with a numbered list whose code fences
are **indented** under the list items, then the corrected module in a column-0
fence. `_FENCE_RE` anchored the CLOSING fence at column 0, so it could not
close an indented fence and instead matched one span running across the prose;
`extract_code` then preferred that blob by length. 12 of 60 rows were scored
against the model's own commentary. The arithmetic is conclusive -- for all 10
affected plain rows, `raw_chars` - `len(reference)` - `len(extracted)` is
between -24 and +82, i.e. the response was exactly *module + the prose that
got extracted*:

| reference | extracted | raw | ref+extracted | delta |
|---|---|---|---|---|
| 8089 | 694 | 8811 | 8783 | +28 |
| 3426 | 364 | 3872 | 3790 | +82 |
| 7677 | 828 | 8531 | 8505 | +26 |
| 10074 | 991 | 11089 | 11065 | +24 |

The complete, correct module was present in every one of those responses.
Re-scoring only those 12 rows with the fixed extractor (`--only-tasks`, $0.56
rather than $2.68 for the whole A/B) recovered **all 12 to `pass=True`,
`fidelity_lev=0.0000`, `delta_cc=0`** -- byte-perfect. That is what produced
the corrected table above, and it **reversed** the pass@1 direction: 27 vs 24
favouring the plain arm, where the broken extractor had shown 17 vs 22
favouring preservation. Both the broken and the corrected artifacts are
committed; `merge_runs.py` rebuilds the corrected one from the three runs.

Three fixes, all pinned by tests:

* `_FENCE_RE` accepts an indented opening **and** closing fence.
* `extract_code` prefers the largest block that **parses as Python**, falling
  back to the largest overall so a genuinely broken repair is still scored as
  broken.
* The **full response text** is now stored in every row (`meta.raw_text`).
  Storing only the extracted code is what made this bug unrecoverable without
  re-spending; a future extractor fix can now re-score any past run offline.

The general lesson is the one the split-by-pass table was added for: a raw
fidelity mean over a mixed population of working and broken repairs is not a
measure of over-editing, and the first time this harness produced a result in
the paper's direction, the result was its own bug.

**Spend:** 60 calls for the A/B (140,606 in / 150,678 out, **$2.682**), 12
recovery calls (**$0.562**), and an earlier hard-shape attempt whose code was
lost before commit (60 calls, **$2.53**). **Total $5.774** against a $6 cap.
No corrected re-run of the full 60 was affordable, and none is needed for the
conclusion above: the recovered rows are byte-perfect, which strengthens
rather than qualifies "the plain arm did not lift off the floor".

Artifacts: `results/tasks_hard_20260913.json` (30 tasks with every node's
reference, corrupted text and gate counts), `results/results_20260913_124736.json`
(the A/B as first scored, broken extractor, kept deliberately),
`results/results_20260913_133450.json` + `results/results_20260913_134056.json`
(the 12 recovery rows), and
`results/results_20260913_134414_corrected.json` + its report (the merged,
corrected run the table above is read from).

## Adding a corruption class

1. In `corrupt.py`, write `_find_<name>(source, tree, idx, rng, file, funcs)`
   returning `Corruption` records. Each must be a **single contiguous
   splice**: `start` (character offset), `original`, `corrupted`. Locate
   nodes with the AST; never re-emit text with `ast.unparse`.
2. Only yield nodes with an enclosing function (`_innermost_function`), and
   skip f-string internals (`_in_fstring`).
3. Register it in `CORRUPTION_CLASSES` and `CORRUPTION_CLASS_NAMES`.
4. `enumerate_candidates` re-parses every candidate and checks the inverse
   restores the exact original; anything failing is counted under
   `syntax_rejects`, which the tests require to be 0.
5. Add the class to `CALC` in `tests/test_edit_fidelity.py` so
   `test_every_class_present_with_exact_inverse_and_byte_identical_remainder`
   covers it.

## What this does not measure

* **It is not the paper's benchmark.** The paper reports 400 problems across
  many repositories; this runs n ~ 20 (easy) or 30 (hard) tasks from one
  repository's modules, inside a size window so the token bill stays bounded.
  Direction can be compared; magnitudes cannot, and nothing here generalises
  beyond this repo.
* **Both shapes returned a null, and the hard one had no power.** Only 9 of 30
  hard-shape pairs were non-tied, which can detect a per-pair preference of
  0.91 at 80% power and nothing smaller. Neither run is evidence against the
  paper; both are evidence that this repo's code, at these task shapes, does
  not give a model room to over-edit.
* **A null here is about the task shape, not the instruction.** Two shapes have
  now failed to lift the plain arm off the floor. A third would need a
  genuinely different kind of defect -- a real bug report rather than an
  inverted mutation, or a file large enough that reproducing it verbatim is
  itself hard -- not merely more of the same knobs.
* **Only bugs the suite can see.** The gate discards anything the target
  cannot detect, so the task set is biased toward the best-tested code paths
  -- and the discard count tells you how much was invisible.
* **Synthetic defects.** Six mutation classes stand in for real bugs; a
  model that recognises "this looks like a mutant" has an easier localisation
  problem than it would on a real defect report.
* **One model, one temperature, one prompt each.** Sampling variance is not
  characterised; the paired sign test in the report is the only inferential
  statistic, and it is on ~20 pairs.
* **Cognitive complexity is a documented subset** of Sonar's rules (no
  recursion, `match`, comprehension clauses; `with` is scored). Compare
  `delta_cc` between arms, not against Sonar's absolute numbers.
* **Levenshtein is over characters,** so a changed trailing newline or a
  re-quoted string costs the same per character as a logic change. That is the
  paper's metric and it is reported as such; the per-task unified diffs in the
  results JSON are there for anyone who wants to look past the number.
* **Cost is an estimate** from a small price table keyed by model family;
  the actual token counts are exact (they come from the API response).
* **A hard kill mid-scoring** cannot run `finally`. The swap writes the
  reference bytes to `<repo>/.edit_fidelity_inflight.json` first and the next
  start-up restores from it (`tasks.recover_inflight`), but between the kill
  and that next start the working tree holds a corrupted module -- `git status`
  will show it.
