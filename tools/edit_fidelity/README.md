# edit_fidelity -- does a repairer edit more than the bug?

An implementation of the measurement in arXiv:2609.04061, *"When Models Edit
Too Much: On the Fidelity of Minimal Code Edits"*, run against **this
repository's own modules and its own pytest targets** rather than a public
benchmark. It exists because the builder's output is graded on green CI, and
green CI cannot see the difference between a one-token fix and a rewrite of the
enclosing function that happens to still pass.

## What it measures

A **task** is `(source_file, test_target, corruption)`. `corrupt.py` injects a
single, seeded, AST-located defect into a live repo file with a **known
inverse** (`comparison_flip`, `boundary_shift`, `boolop_flip`, `arg_swap`,
`guard_drop`, `unary_not_drop`). The splice preserves every other byte of the
file, so the clean file is a legitimate reference: it is exactly what a
minimal repair should reproduce.

A repairer is handed the corrupted file and the failing pytest tail, and its
output is scored against the clean original:

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

### The two poles (self-test)

`run_eval.py --self-test` runs two repairers on live repo files and exits
non-zero unless BOTH hold:

* `OracleRepairer` applies the known inverse. Must score `fidelity_lev == 0.0`
  and pass -- the GREEN pole.
* `SloppyRepairer` restores correctness, then rewrites the enclosing function
  the way an over-editing model does (`ast.unparse` reformat, comments lost,
  locals renamed, an unnecessary re-raising `try/except` added). Must pass the
  tests yet score `fidelity_lev` well above 0 and `delta_cc > 0` -- the RED
  pole. A metric that cannot separate these two is not a metric.

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

## Measured on this repo (2026-09-12, main @ efd2e8d)

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
  many repositories; this runs n ~ 20 tasks from one repository's small,
  well-tested modules (size window: 25+ lines, <= 9 KB, so the token bill
  stays bounded). Direction can be compared; magnitudes cannot, and nothing
  here generalises beyond this repo.
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
