#!/usr/bin/env bash
# SUPERSEDED 2026-04-17 -- do not run.
#
# Original plan: update directive generator prompt to request enrichment
# modules with a single-pass flow. Replaced by staged-evidence approach:
#
#   1. Enrichments expose compute_score(metadata) -> (score, evidence_dict)
#      -- they do not write to DB directly.
#   2. enrichment_harness.py drives 3+ runs with varied synthetic inputs,
#      writing to mcp_signal_enrichments with run_id + input_fingerprint.
#   3. Evidence query (enrichment_evidence.sql) gates which enrichments
#      have proven discrimination worthy of integration.
#   4. Only then are hand-written shims added to protected files.
#
# Replacement patcher: patch_directive_generator_staged.sh (weekend work)
# Design doc: /home/workspace/zo_sentinel/ENRICHMENT_STAGING.md

echo "This patcher has been superseded. See ENRICHMENT_STAGING.md for the"
echo "current staged-evidence approach."
exit 1