# ZO-SENTINEL — Strategic Positioning

## The Core Workflow (not agent-triggered)

Enterprise org receives a request to use an MCP server.
InfoSec team must approve it before deployment.
No agent is involved yet — this is PRE-deployment human review.

```
Developer / team requests use of an MCP
            ↓
   Submit to InfoSec for approval
            ↓
   ZO-SENTINEL produces intelligence brief
   (6 signals, contextual verdict, deployment guidance)
            ↓
   InfoSec analyst reviews judgment + evidence
            ↓
   Decision: Approved / Conditional / Rejected
   (with conditions, caveats, expiry date)
            ↓
   Audit record written (compliance trail)
            ↓
   Approved MCP enters org registry
```

## Why UI-first is strategically correct

The market is saturated with MCP proxy/middleware tools:
- Proxies sit in the data path and filter/monitor at runtime
- They require agent deployment, infra changes, ongoing maintenance
- They solve a different problem: runtime protection

ZO-SENTINEL solves the earlier, distinct problem:
  **Should we allow this MCP at all?**

That question is answered by a human InfoSec analyst, not an agent.
The answer requires intelligence, not a proxy.
The workflow is approval, not filtering.

## Product positioning

"Snyk for MCP servers" — pre-deployment security intelligence
for the enterprise InfoSec approval process.

Not competing with: Invariant Labs, Zed, MCP proxies, runtime monitors
Competing with: manual Google searches, ad-hoc security reviews,
                'we just trust it because it's from a big vendor'

## The verdict language reflects this

Never binary safe/unsafe.
Always contextual: WHO can use this, UNDER WHAT CONDITIONS.

Examples:
- "Likely safe for enterprise use under formal data governance controls"
- "Approved for research use only — not for systems processing PII"
- "Conditional approval: pin to version 1.2.3, review in 90 days"
- "Rejected: tool description contains data exfiltration patterns"

## Phase roadmap (workflow-aligned)

Phase 1: Single lookup UI (done — React artifact)
Phase 2: Approval workflow UI (submit → assess → decide → record)
Phase 3: Org registry (approved MCPs, conditions, expiry)
Phase 4: REST API (for integration into existing InfoSec tooling)
Phase 5: Policy engine (org-specific rules: 'never filesystem on prod')
Phase 6: Enterprise licensing

## Scanner/signal agents (ZOMesh backend)

Running autonomously on ZoComputer — invisible to the enterprise user.
Feeds the intelligence database that powers the UI judgments.
This is the moat: continuously updated, not static.