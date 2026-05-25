# Risk Curve Hypothesis — Design Note

**Status:** Active hypothesis, not yet formalized into roadmap phase.
**Captured:** 2026-04-20
**Originator:** Robin — "wondering if a parabolic curve here might demonstrate risk... finding any sort of relationship between enumerable data points for MCPs and risk would create a secret sauce"

---

## Core idea

Most security scoring treats risk as a linear function of individual signals: more downloads is safer, more age is safer, more contributors is safer. This is probably wrong for MCPs, and may be wrong for package ecosystems in general.

**The hypothesis is that risk is non-linear in many signals, potentially U-shaped or parabolic, and that the specific shape reveals something the ecosystem doesn't yet have language for.**

A parabolic risk curve in `downloads × risk` space would mean:

* **Low end:** New, small, under-scrutinized packages. Classic typosquat and malicious-upload territory. Widely acknowledged.
* **Middle:** Established but not yet juicy. Maintainer attention plus community scrutiny catches most bad behavior. Safest zone.
* **High end:** Ubiquitous dependencies. Maximum blast radius makes them high-value takeover targets. event-stream, colors.js, faker.js, xz utils, SolarWinds, Log4j. This end is LESS acknowledged as a risk zone because "everybody uses it" usually reads as safety.

If the curve is real, the scoring model should not reward popularity monotonically. It should treat extreme popularity as its own risk class, because the attacker's return on compromise increases with dependency depth.

---

## Why this matters for Sentinel specifically

ZO-Sentinel is being built to advise whether an MCP should be trusted. Current signal math (supply_chain, community_signal, temporal_stability, domain_trust) is mostly additive — each signal contributes linearly to a composite score. If risk is genuinely U-shaped in popularity, the composite will systematically under-weight high-end supply-chain-attack risk and over-weight low-end obscurity risk. Both directions of misjudgment matter.

More concretely: a filesystem MCP with 10M weekly npm downloads and broad permissions should NOT necessarily score higher than a new one with narrow permissions, even though every current signal would say it does.

---

## What's enumerable about MCPs that could feed the curve

Signals we already enumerate or can enumerate cheaply:

* **Download count** (npm, pypi) — already in ecosystems_metadata cache
* **Package age** (first_release_published_at) — already cached
* **Contributor count** (GitHub API) — not yet collected
* **Commit frequency / recency** — not yet collected
* **Stargazer count** — not yet collected
* **Open issues / closed issues ratio** — not yet collected
* **Package size** (bytes) — available via npm/pypi metadata
* **Dependency count** (direct + transitive) — available via ecosyste.ms
* **Dependent count** (who depends on this?) — powerful signal, available via ecosyste.ms
* **Permission scope breadth** — available from MCP manifest once we parse it
* **Tool count exposed** — from MCP manifest
* **Version churn** (releases per year) — derivable from package registry
* **Maintainer count / change velocity** — derivable but needs careful collection
* **Download growth curve shape** — requires time-series collection, not just latest snapshot

The last one is the one nobody does well. A sudden parabolic spike in downloads is often either viral adoption OR bot-driven inflation before handover. The shape of the growth curve itself carries signal that point-in-time download count does not.

---

## Three tractable investigations this hypothesis suggests

These all sidestep the fact that we have essentially no labeled "known-malicious MCP" training set yet.

### Investigation 1: Download curve anomaly detection

For every MCP we have a package identifier for, collect monthly download history (ecosyste.ms exposes this; npm and pypi also expose it directly). Compute:

* Shape classification: monotonic growth / monotonic decline / plateau / spike / parabolic spike / reverse-parabolic
* Deviation from cohort: how much does this package's curve diverge from the median curve of packages in its size/age cohort?

Hypothesis: parabolic spikes and reverse-parabolic (rapid decline) curves correlate with incident events (deprecation, takeover, maintainer change). Most MCPs will be monotonic growth or plateau; those are the "safe middle."

Output: curve_shape_signal with distinct values (normal/spike/decline/anomaly). Feeds into composite as a non-linear adjustment.

### Investigation 2: Surface area vs scrutiny ratio

For each MCP compute:

* **Surface area** = function(permission_scope_breadth, tool_count, dependency_count, transitive_dependency_count)
* **Scrutiny** = function(contributor_count, stargazer_count, dependent_count, age_days)

Then plot surface_area / scrutiny. Hypothesis: the top decile of that ratio is where novel risk lives. Packages that ask for a lot and have not yet been examined by many eyes.

Output: scrutiny_deficit_signal. Specifically flags the "broad ambition, small audience" quadrant that current linear scoring doesn't catch.

### Investigation 3: Popularity-inversion check

Take the top 10% of MCPs by download count. For each, check:

* Has the maintainer set changed in the last 90 days?
* Has the package signing key / publishing credential chain changed?
* Has the permission scope expanded in a recent release?
* Is the current maintainer verifiable (DNS, GitHub org membership, corporate domain)?

Hypothesis: high-popularity packages with recent maintainer-chain changes and scope expansions are the supply-chain-attack-in-progress pattern. This is the SolarWinds / xz utils signature.

Output: popularity_inversion_flag. Binary-ish, but rare enough to be actionable when it fires.

---

## What I'd NOT do with this hypothesis

* **Don't fit a curve to current data and call it validated.** We don't have a labeled outcome variable. Any curve we fit would be to a proxy, not to realized risk. That's theater, not science.
* **Don't add a "risk curve signal" to the composite score yet.** The signal layer should be enumerable facts; the curve-shape interpretation is a higher-order analytic that belongs above the signal layer.
* **Don't try to enumerate all signals at once.** Downloads over time is probably the single highest-value addition. Start there.

---

## What this means for roadmap sequencing

Current SENTINEL_ROADMAP_v2 has:

* Phase I: Package metadata (done)
* Phase II: Directory ingestion (in progress today)
* Phase III: Endpoint trust
* Phase IV: Negative signals (threat intel, domain provenance)
* Phase V: Runtime observation

The risk curve hypothesis suggests a **Phase VI or inline supplement**:

**Phase IV.d (or Phase VI): Time-series and shape-based signals.** Collect download history, maintainer-change events, version release cadences as time series, not point-in-time snapshots. Derive shape-classification signals. Feed them into the trust synthesizer as non-linear modifiers rather than additive terms.

This is NOT urgent. It's foundational for a later Sentinel version that actually has differentiated judgment. For now, complete Phase II (directory ingestion) and Phase IV.a (threat intel baseline), then reassess.

---

## Pragmatic next step when ready

When we circle back to this, the smallest useful experiment is:

1. Pick 10 MCPs from mcp_server_registry spanning download volume buckets (3 tiny, 4 medium, 3 large)
2. Manually fetch 12 months of download history for each from npm/pypi
3. Eyeball the curves. Do the shapes tell a story? Do any of the "large" ones have suspicious patterns? Do any of the "tiny" ones show unexpected spikes?
4. If there's any pattern at all, formalize the classification and automate.

That's a 1-hour exploratory session, not a directive. Do it when curiosity strikes, not on a schedule.

---

## Why I'm noting this down instead of building it

Because the hypothesis is more valuable than any specific implementation of it, and if we commit to a specific curve-fitting approach prematurely we'll over-invest in the wrong thing. The value is in the framing:

> **Risk may be where the ratio of surface-area-exposed to scrutiny-applied is anomalous for a package's cohort, and where the temporal shape of an enumerable signal deviates from its cohort's expected trajectory.**

If that framing holds, it reframes every signal we collect as not just "what is the value" but "what is the shape and is it anomalous for its peers." That's a substantially different analytic posture than additive linear scoring, and it's probably what the "secret sauce" ends up looking like.

For today: note captured, no action. Resume Phase II (reference-servers + registry ingestors).