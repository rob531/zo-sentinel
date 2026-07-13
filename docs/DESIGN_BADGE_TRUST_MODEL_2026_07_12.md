# Badge + Claim Flow — Trust Model & Readiness Spec

*Chairman-directed 2026-07-12 ("what if a malicious MCP opts in despite poor
scores, or gets overlooked on scoring — trust but verify"). AGENT-ONLY build
(sensitivity eval, DESIGN_CVE_EXPANSION_AND_INTEGRITY_2026_07_10.md): this is
a keyed/public surface enforcing THE LINE. The factory must not build any of
it. Tag: `design_badge_trust_model_2026_07_12`.*

## Current readiness — NOT functionally ready

- `scorecard_badge_api.py` (built 7/07) is UNMOUNTED by design and contains NO
  freshness/INSUFFICIENT/kill-switch logic — it predates the gate spec and
  must be rewritten, not merely mounted.
- Claim-your-server flow: does not exist (no module, no route).
- `rug_pull_monitor`: NOT running (stale since ~6/20) — must be revived before
  any badge ships, it is the post-badge detection layer.
- Freshness surface: LIVE (mounted 7/10) — the prerequisite that unblocked
  this lane. STALE-gating consumption still unverified end-to-end.

## Core design stance: the badge is a POINTER, not a certificate

The SVG is rendered live by us per request, never cached as a static asset by
the maintainer. It displays tier + scored-date and links the report card. This
single choice defeats most of the threat model: we can change what every badge
in the world says, instantly, without touching anyone's README.

## Threat model → controls

**T1. Malicious server with a wrongly-good score opts in.**
Claiming is not a fast path to legitimacy — it is a fast path to SCRUTINY:
- Claim event ⇒ automatic re-verify BEFORE badge activation: fresh rescore of
  that one server, teacher-model spot-check (cents per event at claim volume),
  vuln-link + threat-ref recheck, adversarial-description probe on its
  metadata. Only a clean re-verify activates the badge. This is the
  trust-but-verify moment: they raised their hand; we look twice.
- Claimed servers join a HIGHER-frequency rescore cohort permanently (they
  asked to display our opinion; our opinion stays current).

**T2. Score was gamed (prompt-injection in metadata, twin laundering).**
The claim-time adversarial probe covers the injection vector at the moment it
matters; the P3 integrity program (canaries, metamorphic pairs, twin
divergence) covers it corpus-wide. A claim-time probe failure ⇒ badge denied +
server flagged for admin review + fixture added to the probe corpus (attackers
donate their techniques to our test suite).

**T3. Rug-pull after badge (turns malicious post-claim).**
- Live-rendered badge inherits every downgrade automatically: score drops ⇒
  badge says so within one render.
- Freshness SLA: data older than SLA ⇒ badge renders STALE (grey, no tier
  claim) — THE LINE applied to the badge itself. Never a green badge on old
  data.
- `rug_pull_monitor` revival is a HARD PRECONDITION (version-delta and
  maintainer-change events trigger event-driven rescore of claimed servers
  first).

**T4. Badge spoofing / wrong-server embedding.**
Badge URL is bound to the server slug; the rendered SVG carries the server
name; report card is one click away and is the source of truth. Optional
later: signed badge query param verified on click. Do not over-engineer v1 —
the pointer design means a spoofed badge links to a report card that
contradicts it.

**T5. False claim (hijacking someone else's listing).**
Ownership proof required: GitHub OAuth with push/admin on the linked repo, or
a well-known file drop in the repo. A claim grants NO influence over scoring
inputs — only badge issuance + dispute standing. All claims audit-logged
(actor, evidence, timestamp). Concrete validation in the addendum below.

### T5 addendum — how OAuth proof is actually validated (2026-07-12)

**Path A — Clerk-mediated GitHub OAuth (primary).**
1. User signs in with the GitHub social connection on our EXISTING Clerk
   instance (no second auth system). We never see or accept a user-pasted
   token; the only token source is Clerk's backend API
   (`GET /v1/users/{uid}/oauth_access_tokens/oauth_github`). Known gotcha:
   api.clerk.com Cloudflare-blocks default urllib UA — send a User-Agent.
2. **The repo of record comes from OUR corpus provenance** (registry-scraped
   source URL), never from the claimant. If they think our linkage is wrong,
   that is a dispute, not a claim — otherwise a claimant could nominate any
   repo they own and "prove" ownership of someone else's listing.
3. At claim init we resolve the repo of record once (unauthenticated
   `GET /repos/{owner}/{repo}`) and pin its **numeric repo id** — ids survive
   renames/transfers; a deleted-and-recreated same-name repo gets a NEW id.
   All checks compare ids, not names, killing the name-squat replay.
4. With the user's token: `GET /repos/{owner}/{repo}` → require
   `.permissions.admin == true` or `.maintain == true` (push alone is too
   weak — drive-by collaborators shouldn't issue badges) AND `.id` matches
   the pinned id. Minimal scopes: none beyond default public read; we request
   NO write scopes ever (outreach doctrine: minimal footprint).

**Path B — nonce file drop (fallback for org-policy-blocked OAuth).**
Single-use nonce, bound to (user, server), 48h TTL. Claimant commits
`.mcprisky-claim` containing the nonce at the ROOT of the DEFAULT branch of
the repo of record; we fetch via raw.githubusercontent (id-pinned repo). A
commit to the default branch requires write+review rights, proving control.
Nonce burned on first verification attempt, pass or fail.

**Both paths then:** evidence JSON persisted (gh login, method, permission
level or nonce commit SHA, repo id, checked_at) + audit_log row.
**Re-verification:** every 90 days AND on maintainer-change/transfer events
from rug_pull_monitor — repos get sold; ownership proof decays. Failed
re-verify ⇒ claim suspended, badge degrades to neutral pointer (fail-closed,
same machinery as `badge.enabled`).

**T6. Dispute-laundering (claim + spam disputes to raise score).**
Disputes remain record-only + admin review (shipped #1378 semantics). A
dispute NEVER auto-changes a score. Dispute volume per claimant is itself a
signal surfaced to admin.

**T7. Our own failure (overlooked mis-score embarrasses a badge).**
- `badge.enabled` policy kill-switch (policy.py layer): one flip degrades ALL
  badges to neutral "view report" pointers — fail-closed, no tier claims.
- Distribution sentinel + canary alerts block model_version promotion; badges
  render from the last GOOD model_version only.
- Public incident norm: if we mis-scored, the report card says so and the
  dispute ledger shows the correction. Auditable fallibility IS the trust
  product.

## Build order (all agent-emitted PRs)

1. Revive + verify `rug_pull_monitor` (precondition, also fixes known-stale daemon).
2. Rewrite `scorecard_badge_api.py`: live SVG, tier+date, STALE/INSUFFICIENT
   states, `badge.enabled` kill-switch, freshness consumption. Mount dark.
3. Claim flow: GitHub-OAuth ownership proof, audit log, claim⇒re-verify
   pipeline (rescore + teacher spot-check + adversarial probe), badge
   activation only on clean verify.
4. Claimed-cohort rescore cadence + event triggers.
5. Smoke the kill-switch ON THE MACHINE (arm = flip it and watch the badge
   degrade — "switch had no lever" lesson), then announce per outreach
   doctrine.

## Explicit rejections

- Static/cached badge assets — rejected (revocation must be instant).
- Badge issuance without claim-time re-verify — rejected (T1).
- Any score mutation via claim or dispute — rejected (T6).
- Shipping before rug_pull_monitor is live — rejected (T3).
