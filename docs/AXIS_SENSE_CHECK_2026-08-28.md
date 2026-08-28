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
