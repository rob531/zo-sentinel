# The MCP Landscape — ecosystem facts from 334,754 scored servers

*2026-10-19. Source: MCPRisky v3.4 scoring of 334,754 live MCP servers across 7 risk axes. These are
our risk model's assessments of the live corpus (not hand audits), framed for external use.*

## The headline numbers (marketing-ready)

- **85% of MCP servers expose no verifiable authentication.** auth_strength: 85.3% UNKNOWN, 9.6% WEAK,
  only 5.1% MODERATE — and **zero** reach the top tier.
- **The MCP ecosystem is almost entirely anonymous: 97.5% of servers have no identifiable publisher.**
  Of 334,754 servers, just 8,088 (2.4%) are from an established maintainer, 168 community-backed, and
  **exactly 2 are from a verified publisher.** Two.
- **Nearly everything can reach the open internet: 98.5% have outbound network egress** (88.5% external,
  10% arbitrary). Only 228 servers (0.1%) have no egress at all.
- **Two-thirds (65.7%) handle sensitive or critical data** (56.6% sensitive, 9.1% critical).
- **Only 1.4% of MCP servers are low-risk.** 63.9% medium, 30.7% high, 4.0% (13,295 servers) critical —
  so **~1 in 3 is high-or-critical risk.**

## The pattern that matters most (architectural)

- **Nearly 1 in 4 servers (24.4% — 81,721 of them) combine all three danger signals at once: broad
  capability + external egress + sensitive-data access.** That triad — a server that can do a lot, reach
  out freely, and touches sensitive data — is the highest-concern architectural profile, and a quarter of
  the ecosystem fits it.
- **The riskiest servers are the least protected: of the 116,094 high-or-critical servers, 84% (97,850)
  are completely unauthenticated.** Risk and lack-of-auth concentrate together, not apart.
- Capability breadth: 26.2% broad, 56.6% moderate, 17.1% narrow. Exploit surface: 16.4% broad.

## Framing notes (for accurate external use)
- "No verifiable authentication" = no observable/declared auth signal — defensible as *verifiable*, not a
  claim the server is definitely open. Say "verifiable/observable."
- These are **model assessments** (our product's output) — lead with "MCPRisky's model finds…", which is
  exactly the proof point of the product.
- v3.4 is **scored-complete**; the verdict cutover is still gated — fine for "our scoring finds," not for
  "mcprisky.io currently shows."

## What still needs net-new / lifecycle data (your original examples)
Growth/registration trends, **region** shifts, **cert/CA** changes, new-publisher velocity — these are
**temporal/ecosystem-change** patterns that this static re-score cannot show. They unlock with the **v5
lifecycle** work: the BORN route (net-new intake) enables growth/region trends; the CHANGED route surfaces
cert/CA deltas; DIED/known-bad builds the "what got taken down and why" intel. So the *change* stories are a
v5 deliverable, not available from the current corpus.
