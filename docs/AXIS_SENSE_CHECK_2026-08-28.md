# Axis sense-check — 2026-08-28

> **CORRECTION, same day.** The first version of this document analysed the
> **wrong plane**. Its numbers came from the mesh bus (`:8772`), which holds
> **3,244 registry rows and 1,930 scored servers**. The real scored corpus is
> **66,565 servers / 465,955 score rows / 80,539 registry rows** on the app
> Postgres (measured 2026-07-15 from prod `/freshness`, `docs/PLAN_200K.md`).
> That is **~2.9%** of the scored corpus, and the sample is not merely small —
> it is **unrepresentative in the opposite direction** (see §0).
>
> Both planes carry the same table names, which is how this went unnoticed.
> Section 0 records what survives the correction and what is withdrawn.

## 0. Which plane, and what that costs

`mcp_server_registry` and `mcp_llm_axis_scores` exist on **two planes with
identical names** — the mesh bus DuckDB and the app Postgres. Everything below
§0 was measured on the bus. The app plane is not reachable from this container:
no `DATABASE_URL` in the environment, and the prod API (`mcprisky.io`) is
auth-gated on every data route.

| | mesh bus (measured) | app Postgres (`PLAN_200K`, 2026-07-15) |
|---|---:|---:|
| registry rows | 3,244 | **80,539** |
| scored servers | 1,930 | **66,565** |
| axis score rows | 13,498 | **465,955** |

**The distributions do not transfer, and the direction matters.** `overall_risk`
on the bus is MEDIUM 60% / HIGH 33% / CRITICAL 5% — 38% in the top two bands.
The documented prod distribution (FU-058, `PRODUCT_SPEC.md`) is CRITICAL 65,269 /
HIGH 106,118 / MEDIUM 854 / LOW 54 — **99.47% in the top two bands**. Opposite
shape. So the bus is not a random sample of prod, and no percentage in §2 or §3
should be read as a corpus figure.

### What survives the correction

- **Finding 1 (no ignorance token).** A property of the *label vocabulary*, not
  of any sample. Both planes were scored by the **same model version,
  `v3.0_40974559`** — the bus rows carry it and `PLAN_200K` names it for the
  66,565 corpus — so the vocabulary is the same. It also *explains* the prod
  distribution better than it explains the bus one: a scorer that cannot say
  "insufficient evidence" has to put its mass somewhere, and on prod it went to
  HIGH/CRITICAL.
- **Finding 3 (no `schemas/risk_axis_mapping_v1.json`).** A repository fact.
- **The tool.** `tools/axis_sense_check.py` is plane-agnostic; it needs a
  connection to the app plane, not a rewrite.

### What is WITHDRAWN

- **The original Finding 2, "`mcp_tool_hashes` is empty (0 rows)", as a
  corpus-wide claim.** True on the bus, unknown on prod. It is replaced by §1
  below, which is a sharper and independently verified version of the same
  problem.
- **All per-axis percentages, the mutual-information figures, and the Gemini
  agreement rates**, as statements about the corpus. They stand as statements
  about the bus plane and as a demonstration that the method works.

---

## 1. The evidence layer records the hash of nothing

This replaces the withdrawn Finding 2, and it is stronger because it is a
constant rather than an absence.

`mcp_fingerprints` covers **all 1,930 scored servers** (3,316 rows, 3,316
distinct servers). It is where the tool-level evidence is supposed to live.
Across every one of those rows:

| column | distinct values over 3,316 rows |
|---|---:|
| `tool_name_hash` | **1** |
| `permission_scope_hash` | **1** |
| `domain_fingerprint` | 10 (two values cover 89%) |

That single value, for both hash columns, is:

```
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

which is **`SHA-256("")` — the hash of the empty string**, verified.

So the fingerprinter hashed an empty input for every server and stored the
result as a fingerprint. `auth_strength` is 83–88% UNKNOWN not because the axis
is badly posed but because **its input is a constant**, and a constant that
looks exactly like a computed hash. Nothing downstream could tell.

This is the same failure shape as the empty-catalog case in the SQL-string
lint: a degenerate input that is indistinguishable from a real one, producing a
verdict nobody can challenge.

## 2. Baseline (BUS PLANE ONLY — see §0) — measured on the live bus, 1,928 scored servers

| axis | dominant label | share | labels used | normalised MI with `overall_risk` |
|---|---|---:|---:|---:|
| auth_strength | **UNKNOWN** | 83% | 3 | **0.02** |
| maintainer_trust | **UNKNOWN_AUTHOR** | 92% | 2 | **0.06** |
| network_egress | EXTERNAL | 84% | 5 | 0.13 |
| data_sensitivity | SENSITIVE | 63% | 5 | 0.23 |
| capability_breadth | MODERATE | 59% | 3 | 0.26 |
| exploit_surface | MODERATE | 64% | 4 | 0.34 |

Two of six input axes carry almost no information about the verdict, and their
dominant label is a **non-answer** rather than a risk level.

## 3. The pilot result (BUS PLANE ONLY — see §0)

| axis | agreement | incumbent UNKNOWN | Gemini INSUFFICIENT | verdict |
|---|---:|---:|---:|---|
| overall_risk | **4%** | 0% | **80%** | INCUMBENT_OVERCONFIDENT |
| auth_strength | 0% | 88% | 96% | EVIDENCE_STARVED |
| maintainer_trust | 16% | 96% | 68% | EVIDENCE_STARVED |
| exploit_surface | 28% | 0% | 44% | INCUMBENT_OVERCONFIDENT |
| data_sensitivity | 36% | 0% | 40% | INCUMBENT_OVERCONFIDENT |
| capability_breadth | 40% | 0% | 28% | DISCRIMINATING |
| network_egress | 52% | 8% | 40% | INCUMBENT_OVERCONFIDENT |

## 4. The original findings, as written before the plane error was caught

### 1. Five of seven axes have no way to say "I cannot tell"

`auth_strength` and `maintainer_trust` return UNKNOWN 88% and 96% of the time —
**only because someone put an UNKNOWN token in those two label sets.** The other
five have no ignorance token, so they never return one, and their labels are
therefore indistinguishable from informed ones.

`overall_risk` is the sharpest case: the incumbent assigns a confident tier to
**every** server; a second scorer on identical evidence declines to rate **80%**
of them. Agreement is **4%**.

This is the repo's own recurring repair — *make "I could not evaluate this"
distinguishable from "this is fine", and make the distinction load-bearing* —
now found in the scoring layer, where it has been silently manufacturing tiers.
It is also why `overall_risk` escalated **1,136 of 1,929** servers: escalation is
firing on labels that mostly encode absence of evidence.

### 2. `mcp_tool_hashes` is empty — 0 rows  
**WITHDRAWN as a corpus claim — see §0 and §1.**

All 1,930 servers were scored on registry metadata alone: name, url,
registry_source, and a one-line description. Four of the axes ask about *tool
behaviour* — auth strength, capability breadth, network egress, exploit surface
— and none of that evidence exists.

**Both scorers independently agree the evidence cannot answer `auth_strength`**
(88% vs 96%). That is the strongest result here, because two unrelated
instruments reached it separately. No rubric rewrite touches it; it is an
ingestion problem.

### 3. `schemas/risk_axis_mapping_v1.json` does not exist

`PRODUCT_SPEC.md` instructs consumers to read axis label enums from this
contract. It is **not on main**. So every consumer infers the enum from observed
data, and *"this axis only ever emits 2 labels"* cannot be distinguished from
*"this axis declares 2 labels"* — mode collapse versus a correctly narrow axis,
which need opposite fixes.

This trap is live, and this analysis fell into it: the first version of
`axis_sense_check.py` hand-wrote the label sets, and **4 of 7 were wrong** —
`auth_strength` was offered NONE and STRONG, which the incumbent can never emit.
The tool now reads the sets from the bus. Guessing an enum is exactly what
PRODUCT_SPEC warns has burned this codebase before.

## Gemini declines selectively, not reflexively

It answers confidently where the description is substantive, which is what makes
the refusals meaningful:

| server | incumbent | Gemini |
|---|---|---|
| `favcrm/favcrm` | HIGH | **HIGH** — customer, financial and communication data |
| `qbtlabs/openmm-mcp` | HIGH | **CRITICAL** — exposes account and trading tools to agents |
| `tailwindcss-mcp-server` | LOW | INSUFFICIENT_EVIDENCE |
| `009alok/Spring-AI-MCP-Client` | HIGH | INSUFFICIENT_EVIDENCE — **"describes an MCP client, not a server"** |

The last row is a data-quality catch the incumbent missed: a *client* in the
registry, scored HIGH as though it were a server.

## What this says to do, in order

1. **Add an explicit `INSUFFICIENT_EVIDENCE` label to every axis**, distinct
   from any risk level, and make the verdict layer refuse to escalate on it.
   Cheapest change, largest effect, and it stops the tier layer manufacturing
   confidence.
2. **Write `schemas/risk_axis_mapping_v1.json`.** Until it exists, no claim that
   an axis has collapsed can be substantiated.
3. **Fill `mcp_tool_hashes`.** Four axes are unanswerable without tool
   manifests, and two are provably so.
4. Only then revisit the rubric. On this evidence `capability_breadth`,
   `data_sensitivity` and `exploit_surface` are working; the problem is
   upstream of the wording.

## Caveats

- n=25 servers. Directionally strong (0%/4% agreement is not a sampling
  artefact) but the per-axis percentages will move; re-run at n=200 before
  acting on any single figure.
- **Agreement is not accuracy.** Two instruments can be wrong the same way. Only
  the disagreements and the admitted-ignorance rates are evidence here.
- Gemini's refusal rate is a property of the prompt as well as the evidence; the
  prompt explicitly legitimises refusal. That is deliberate — the incumbent's
  cannot — but it means the *absolute* rate is not directly comparable, while
  the auth_strength agreement between the two (88%/96%) is.

Reproduce: `python3 tools/axis_sense_check.py --sample 25`

---

# 5. The fix — grounding the scorer in the signal history

`mcp_signal_scores` holds **10.4M rows over 3,173 servers**, each with a score
**and an evidence payload**. **1,874 of the 1,930 v3-scored servers have
signals.** The evidence to grade several axes was already there and unread —
v3 scores from a name, a URL and a one-line description, and has no evidence
column at all.

`tools/axis_scorer_grounded.py` copies the signal layer's shape exactly:
`{score, evidence:{source, checks, raw}}`. Four rules, three of which exist
because a draft of this fix got them wrong.

**1. Degeneracy is measured every run, never assumed.** A signal earns the right
to move a label by having spread across the corpus *today*:

| rejected | why |
|---|---|
| `tool_count` | sd 1.36, 2 distinct scores — it reads `mcp_fingerprints.tool_count`, which is the empty-hash table from §1 |
| `known_bad_pattern` | sd 0.68 |
| `injection_resilience` | sd 0.25, 0.9% coverage |
| `permission_scope`, `tool_description_safety`, `temporal_stability`, `community_signal` | 0.9–1.0% coverage — canaries, not production signals |

**2. No usable backing signal → `INSUFFICIENT_EVIDENCE`.** Not a middle label,
not the prior.

**3. A derived signal is not evidence.** `reputation`'s evidence is
`{"checks":["existing_trust:X"],"existing_trust":X}` on every row sampled — its
only check *is* a stored trust value. `composite` is an aggregate of the others.
Both have real spread, so the degeneracy test passes them, and both are
circular.

**4. The evidence travels with the label.**

## Result, 150 servers × 7 axes

| axis | v3 UNKNOWN | v4 INSUFFICIENT | v4 labels used | changed |
|---|---:|---:|---:|---:|
| auth_strength | 86% | 77% | 1 | 98% |
| capability_breadth | 0% | **100%** | 0 | 100% |
| data_sensitivity | 4% | **100%** | 0 | 100% |
| network_egress | 7% | 0% | 3 | 99% |
| maintainer_trust | 84% | 77% | 3 | 94% |
| exploit_surface | 0% | 0% | 2 | 96% |
| overall_risk | 0% | 0% | 2 | 82% |

Two things changed, in opposite directions:

- **`capability_breadth` and `data_sensitivity` now abstain on everything.**
  Their only backing signals are the empty-hash `tool_count` and a 1%-coverage
  canary. v3 emitted a confident label for all 150 servers on each.
- **`maintainer_trust` and `network_egress` now discriminate** where evidence
  exists. A worked case: 999 GitHub stars, domain age 1 day → `COMMUNITY`,
  `confidence 0.465`, both signals and their raw evidence attached. **v3 called
  it `UNKNOWN_AUTHOR`.**

## Three bugs caught in this fix, recorded rather than quietly patched

1. **Label direction.** Five label sets ascend in risk (`LOW`→`CRITICAL`); two
   ascend in *goodness* (`NONE`→`STRONG`, `UNKNOWN_AUTHOR`→`ESTABLISHED`).
   Mapping a risk score onto the second kind without reversing labels the
   riskiest servers `ESTABLISHED`. It changed 40% of `maintainer_trust` verdicts.
   Direction is now **required** in the contract and missing it raises — it
   cannot be inferred from the words, because `NONE` is the safest value of
   `network_egress` and the worst value of `auth_strength`.
2. **Circular evidence.** Before rule 3, `maintainer_trust` abstained 0% and
   looked like a triumph — because **110 of 150 servers were graded
   `ESTABLISHED` on `reputation` alone, with no `github_stars` at all**. It read
   exactly like the fix working. With `reputation` rejected, abstention is 77%,
   which is the honest number.
3. **Band mapping is uncalibrated** — equal-width bands on the inverted score.
   Not yet fixed, and it is why `auth_strength` emits a single label where it
   answers at all: `tls_validity` averages 50.6 with sd 4.5, so every server
   lands in the same band. Calibrating against the score distribution is the
   next pass.

`schemas/risk_axis_mapping_v1.json` now exists — the contract PRODUCT_SPEC has
told consumers to read since before it was written. Every axis declares
`INSUFFICIENT_EVIDENCE`, its backing signals, and its direction.

**Still the bus plane.** These are 150 of 1,930 bus servers, not the 66,565 on
the app plane. The rules are plane-independent; the percentages are not.

---

# 6. Root cause, fixed — and the bands calibrated

## 6a. The empty hash traced to its source

`mcp_fingerprinter.get_server_tools()` used to `SELECT` five per-tool columns
from **`mcp_tool_definitions` — a table that exists on no plane and never did**.
#4123 corrected which table it reads (`mcp_tool_hashes`). **It did not stop an
empty result producing a valid-looking hash**, so the defect survived the
repair:

```python
tool_names       = extract_tool_names(tools)          # tools == []  ->  []
tool_name_hash   = compute_sha256_hash(','.join([]))  # -> SHA-256("")
```

The full chain, now traced end to end:

```
mcp_tool_definitions never existed
  -> get_server_tools() returns []
    -> hashing "" yields a well-formed 64-hex constant
      -> all 3,316 mcp_fingerprints rows carry it in BOTH hash columns
        -> the tool_count signal scores 91.95 +/- 1.36 for every server
          -> capability_breadth and auth_strength have no real evidence
            -> and v3, with no ignorance token, labels them confidently anyway
              -> 99.47% of the prod corpus lands in the top two risk bands
```

**Fix**: `hash_or_absent()` returns `None` for empty content, and
`is_absent_hash()` lets consumers treat the 3,316 rows already written as
absent — **non-destructive; nothing is deleted**. A hash of nothing must be
`None`, so that a consumer can tell the difference.

## 6b. Bands calibrated against the distribution

Equal-width bands assume the scores use the 0–100 scale. They do not.
`calibrate()` places cut points at empirical quantiles and **publishes them**,
and refuses to band at all when the corpus spread cannot separate the labels
(IQR < 5).

| axis | calibration | effect |
|---|---|---|
| auth_strength | **NOT BANDABLE** | `tls_validity` is 50.6 ± 4.5 — it cannot separate 4 auth levels. Was emitting one label for 23% of servers while looking like a four-way judgement; now abstains on 100%. |
| exploit_surface | cuts [0.0, 5.0, 13.5], IQR 13.5 | **2 → 3 labels** — discrimination the flat bands had hidden |
| overall_risk | cuts [10.0, 10.0, 22.5], IQR 12.5 | **2 → 3 labels** |
| network_egress | cuts [20.0, 20.0, 32.5], IQR 12.5 | 3 labels; disagreement with v3 fell 99% → 50% |
| maintainer_trust | cuts [48.5, 48.5], IQR 40.0 | 2 labels over the 34 servers with real evidence |

It deliberately does **not** force a uniform spread. Pure quantile banding would
put a fixed share in `CRITICAL` forever — a different way of manufacturing
confidence, and close to how prod reached 99.47%.

**Known limitation:** several cut lists contain duplicates (`[20.0, 20.0, 32.5]`,
`[48.5, 48.5]`), which leaves a band empty. That is real — the underlying scores
are coarse, taking only 4–9 distinct values — and it is reported rather than
smoothed away. Finer scores are an upstream fix, not a banding one.

## Final state

| axis | v4 verdict | why |
|---|---|---|
| auth_strength | **abstains 100%** | no usable signal; not bandable |
| capability_breadth | **abstains 100%** | only `tool_count` (empty-hash) and a 1% canary |
| data_sensitivity | **abstains 100%** | same |
| network_egress | 3 labels | `domain_trust`, `url_safety` |
| maintainer_trust | 2 labels, abstains 77% | `github_stars`, `domain_age` |
| exploit_surface | 3 labels | `tool_security`, `otx_threat_intel` |
| overall_risk | 3 labels | `supply_chain`, `domain_trust` |

Three axes now say "I cannot tell" instead of inventing 150 confident labels
each. Four discriminate on evidence they carry with them.

## Still open, named not parked

- **`mcp_tool_hashes` is empty (0 rows)**, so the fingerprinter fix is
  correct-but-unexercised. Nothing crawls MCP servers for their tool manifests.
  Until something does, three axes will keep abstaining — **which is now the
  honest output rather than a hidden one.**
- **The enrichment lane died 2026-06-11.** All six enrichment signals stop
  within 2 seconds of each other (12:04:14–12:04:16), so it was one process, not
  six failures. `enrichment_dispatcher_daemon.py` is declared in **neither**
  `go.sh` nor `watchdog.sh`, so nothing ever restarted it. It has three stacked
  defects: it fetches from `/registry/mcp_servers` (**404** — and a 404 does not
  raise, so it silently returns `[]` and logs "skipping cycle" forever); it posts
  `{"records": ...}` where the write service expects `{"rows": ...}`; and it
  dispatches 3 of its 4 declared enrichers. Not restarted — it would produce
  nothing, and restarting a daemon whose input is a 404 is how the promoter went
  unnoticed for ten days.
- **A daemon declared nowhere is invisible to the drift check**, which diffs
  declared against running. This lane is the proof case.
