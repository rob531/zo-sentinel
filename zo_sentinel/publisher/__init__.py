"""
publisher -- the goose -> GitHub-PR bridge for the zo-sentinel build loop.

Today the autonomous builder writes artifacts in-place on the host (build_artifact
mesh row -> gate_8 -> promoters -> live), and the GitHub E2E gates (ruff /
smoke-ladder / frontend) only ever run on PRs a human opens. This package closes
that gap: it watches `build_artifact` rows and opens a *gated* PR per artifact,
so every autonomous build runs through the same E2E gates before merge.

Dormant by design (two-latch, like the ingestor/governor): nothing is pushed
until `.pr_publisher_enabled` exists (or PR_PUBLISHER_ENABLED is set). It reuses
the ingestor's store seam, BuildArtifact model, and static safety scan.
"""
