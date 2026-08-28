# Axis sense-check — 2026-08-28

Does the MCP scoring axis need refinement? Yes — but not in the way the question
implies. The rubric is not the binding constraint.

Method: a **second, independent scorer** (Gemini 2.5 Flash) was given **exactly
the evidence the incumbent had**, **blind** to the incumbent's label, as **one
discrete task per (server, axis)** — never seven axes in one prompt, so an early
judgement cannot colour the rest. It was allowed to return
`INSUFFICIENT_EVIDENCE` as a first-class verdict. 25 servers × 7 axes =
175 tasks, 0 errors. Tool: `tools/axis_sense_check.py`.

## Baseline — measured on the live bus, 1,928 scored servers

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

## The pilot result

| axis | agreement | incumbent UNKNOWN | Gemini INSUFFICIENT | verdict |
|---|---:|---:|---:|---|
| overall_risk | **4%** | 0% | **80%** | INCUMBENT_OVERCONFIDENT |
| auth_strength | 0% | 88% | 96% | EVIDENCE_STARVED |
| maintainer_trust | 16% | 96% | 68% | EVIDENCE_STARVED |
| exploit_surface | 28% | 0% | 44% | INCUMBENT_OVERCONFIDENT |
| data_sensitivity | 36% | 0% | 40% | INCUMBENT_OVERCONFIDENT |
| capability_breadth | 40% | 0% | 28% | DISCRIMINATING |
| network_egress | 52% | 8% | 40% | INCUMBENT_OVERCONFIDENT |

## Three findings, in order of how much they cost

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
