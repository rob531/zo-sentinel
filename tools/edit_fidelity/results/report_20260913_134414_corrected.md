# Edit-fidelity eval -- 20260913_134414_corrected

Repo `D:\zo\_staging_clones\edit-fidelity` @ `160ce89`; seed 20260912; model `claude-sonnet-4-5-20250929`; task shape **hard** (nodes per task [2, 3], enclosing function >= 10 lines, prompt withholds the enclosing function, which test fails, and the pytest tail).

## Validity gate (task discovery)

- file size window: 60+ lines, <= 11000 bytes
- pairs (source, test) considered: 23
- candidates tried: 371
- VALID tasks (test green on clean, RED on corrupted): 30
- DISCARDED, tests stayed green (suite cannot see the mutation): 264
- discarded, timeout: 0
- pairs skipped, clean run not green / zero collected: 0
- pairs skipped, clean run over time budget: 0
- corrupt.py syntax rejects (must be 0): 0
- candidates skipped, enclosing function under 10 lines: 103
- individually-red components: 107; combos tried: 30; combos green (discarded): 0
- nodes per task: {"2": 21, "3": 9}; distinct functions per task: {"1": 25, "2": 5}; mean enclosing-function length: 36.5 lines
- tried by class: {"arg_swap": 110, "boolop_flip": 42, "boundary_shift": 110, "comparison_flip": 34, "guard_drop": 61, "unary_not_drop": 14}
- red components by class: {"arg_swap": 42, "boolop_flip": 20, "boundary_shift": 16, "comparison_flip": 3, "guard_drop": 17, "unary_not_drop": 9}
- green(discarded) by class: {"arg_swap": 68, "boolop_flip": 22, "boundary_shift": 94, "comparison_flip": 31, "guard_drop": 44, "unary_not_drop": 5}

## Results

| arm | n | scored | errors | fidelity_lev mean | median | max | delta_cc mean | delta_cc>0 | pass@1 |
|---|---|---|---|---|---|---|---|---|---|
| anthropic_plain | 30 | 30 | 0 | 0.0025 | 0.0000 | 0.0273 | -0.333 | 0 | 0.9000 (27/30) |
| anthropic_preserve | 30 | 30 | 0 | 0.0021 | 0.0000 | 0.0273 | -0.333 | 0 | 0.8000 (24/30) |

### Fidelity split by pass@1 outcome (over-editing and breaking are different things)

A repair that BREAKS the file also scores far from the reference. Only the passed column can be read as over-editing; the failed column is a different phenomenon and is reported separately so the two cannot be confused.

| arm | passed n | fidelity mean (passed) | delta_cc mean (passed) | byte-perfect (passed) | failed n | fidelity mean (failed) |
|---|---|---|---|---|---|---|
| anthropic_plain | 27 | 0.0015 | -0.296 | 22 | 3 | 0.0112 |
| anthropic_preserve | 24 | 0.0009 | -0.208 | 20 | 6 | 0.0072 |

## Paired comparison: anthropic_preserve vs anthropic_plain (same tasks)

- n paired: 30
- fidelity_lev: preserve lower on 4, higher on 5, ties 21; sign-test p = 1.0000
- delta_cc: preserve lower on 0, higher on 0, ties 30; sign-test p = UNKNOWN
- pass@1: preserve 24 vs plain 27 (of 30)

- pass@1 is paired too, so it gets a paired test: McNemar exact over the 3 discordant task(s) (preserve-only 0, plain-only 3, concordant 27); p = 0.2500

### Power of this sample (two-sided sign test, alpha 0.05)

- non-tied fidelity pairs: 9 of 30
- power to detect a per-pair preference of 0.65 / 0.75 / 0.85 for the preservation arm: 0.1225 / 0.3004 / 0.5995
- smallest per-pair preference detectable at 80% power with this many non-tied pairs: 0.91
- the paper reports means (0.195 -> 0.131), not a per-pair win rate, so the 0.65-0.85 grid is an assumption stated here rather than the paper's effect size; a null with low power at 0.75 is 'n too small to tell', not evidence of absence.

## Model usage

```
{
 "model": "claude-sonnet-4-5-20250929",
 "calls_total": 60,
 "input_tokens": 140606,
 "output_tokens": 150678,
 "est_cost_usd": 2.682,
 "pricing_known": true,
 "calls_per_arm": {
  "anthropic_plain": 30,
  "anthropic_preserve": 30
 }
}
```
