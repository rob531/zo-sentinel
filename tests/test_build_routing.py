"""
test_build_routing.py -- hermetic tests for the goose build-path glue.

Covers the pure logic the host daemons (goose_runner, builder_mcp) delegate to:
complexity -> ladder alias, the per-directive env, and the build_artifact row
schema the live build now emits. Includes a drift guard asserting every alias is
a real escalation.MODEL_TASK_MAP key (so #16 routing and this stay in sync).
"""
from __future__ import annotations

import json

import escalation
from zo_sentinel.build_routing import (
    BUILDER_AGENT_ID,
    COMPLEXITY_TO_ALIAS,
    build_artifact_row,
    build_env_for,
    directive_content,
    resolve_directive_id,
    tier_for_complexity,
)


def test_tier_for_complexity():
    assert tier_for_complexity("low") == "zo-ladder-low"
    assert tier_for_complexity("medium") == "zo-ladder-medium"
    assert tier_for_complexity("high") == "zo-ladder-high"
    assert tier_for_complexity("critical") == "zo-ladder-critical"
    # unknown / missing / garbage all fall back to rung-0 (prior behaviour)
    assert tier_for_complexity("unknown") == "zo-ladder-low"
    assert tier_for_complexity(None) == "zo-ladder-low"
    assert tier_for_complexity("") == "zo-ladder-low"
    assert tier_for_complexity("WEIRD") == "zo-ladder-low"
    assert tier_for_complexity(" Medium ") == "zo-ladder-medium"  # normalised


def test_aliases_match_escalation_map():
    """Drift guard: every routing alias must be a real ladder task alias (#16)."""
    for alias in COMPLEXITY_TO_ALIAS.values():
        assert alias in escalation.MODEL_TASK_MAP, f"{alias} missing from MODEL_TASK_MAP"


def test_build_env_for_pins_orchestrator_and_routes_codegen():
    env = build_env_for({"complexity": "medium", "key": "build_x", "phase": "p2"})
    # medium pins to zo-ladder-medium (= the MiniMax-M3 rung) -- still ONE coherent
    # model per build, just stronger. low/high/critical stay on rung-0 M2.7.
    assert env["GOOSE_MODEL"] == "zo-ladder-medium"      # M3-pinned builder
    assert env["ZO_BUILD_TIER"] == "zo-ladder-medium"    # codegen tier (routed)
    assert env["ZO_BUILD_TASK"] == "build_x"
    assert env["ZO_BUILD_PHASE"] == "p2"
    # only medium gets M3; everything else stays pinned to rung-0 M2.7
    assert build_env_for({"complexity": "high"})["GOOSE_MODEL"] == "zo-ladder-low"
    assert build_env_for({"complexity": "low"})["GOOSE_MODEL"] == "zo-ladder-low"


def test_build_env_default_is_rung0():
    env = build_env_for({})                              # no complexity/key/phase
    assert env["GOOSE_MODEL"] == "zo-ladder-low"
    assert env["ZO_BUILD_TIER"] == "zo-ladder-low"
    assert env["ZO_BUILD_TASK"] == "" and env["ZO_BUILD_PHASE"] == ""


def test_build_env_task_fallback_chain():
    assert build_env_for({"directive_id": "d9"})["ZO_BUILD_TASK"] == "d9"
    assert build_env_for({"id": 42})["ZO_BUILD_TASK"] == "42"


def test_directive_content_uses_description_for_generator_directives():
    # the exact shape the generator writes (no content/goal/spec)
    d = {"task": "build_domain_trust_enrichment", "handler": "generate_file",
         "output_file": "domain_trust_enrichment.py", "complexity": "medium",
         "description": "NEW enrichment module exposing compute_score(metadata)."}
    c = directive_content(d)
    assert c is not None                       # would have been DROPPED before
    assert "domain_trust_enrichment.py" in c   # target file threaded in
    assert "compute_score" in c                # spec preserved


def test_directive_content_precedence_and_empty():
    assert directive_content({"content": "C", "description": "D"}) == "C"
    assert directive_content({"goal": "G"}) == "G"
    assert directive_content({"spec": "S"}) == "S"
    assert directive_content({"description": "just a desc"}) == "just a desc"  # no output_file
    assert directive_content({"task": "t", "handler": "x"}) is None   # nothing usable


def test_resolve_directive_id_falls_back_to_task():
    # generator directives carry only `task` -> must NOT collapse to "unknown"
    assert resolve_directive_id({"task": "build_domain_trust_enrichment"}) == \
        "build_domain_trust_enrichment"
    # two distinct tasks resolve to two distinct ids (the actual bug)
    a = resolve_directive_id({"task": "build_x"})
    b = resolve_directive_id({"task": "build_y"})
    assert a != b


def test_resolve_directive_id_precedence():
    assert resolve_directive_id({"directive_id": "D", "id": "i", "key": "k",
                                 "task": "t"}) == "D"
    assert resolve_directive_id({"id": "i", "key": "k", "task": "t"}) == "i"
    assert resolve_directive_id({"key": "k", "task": "t"}) == "k"
    assert resolve_directive_id({"task": "t"}) == "t"
    assert resolve_directive_id({}) == "unknown"
    assert resolve_directive_id({"id": 158639}) == "158639"   # numeric id stringified


def test_build_artifact_row_schema():
    row = build_artifact_row(
        file="foo_enrichment.py", content_bytes=123, context_type="enricher",
        tier="builder_high", model="zo:openai/gpt-5.4-mini", backend="zo_routed",
        phase="p1", task="build_foo", built_at="2026-05-30T00:00:00Z",
    )
    assert row["agent_id"] == BUILDER_AGENT_ID
    assert row["memory_type"] == "build_artifact"
    c = json.loads(row["content"])
    # base schema the ingestor reads
    assert c["file"] == "foo_enrichment.py" and c["bytes"] == 123
    assert c["interface"] == "enricher" and c["task"] == "build_foo"
    assert c["phase"] == "p1" and c["built_at"] == "2026-05-30T00:00:00Z"
    # new ladder provenance the publisher surfaces
    assert c["tier"] == "builder_high"
    assert c["model"] == "zo:openai/gpt-5.4-mini" and c["backend"] == "zo_routed"


def test_build_artifact_row_defaults_built_at():
    row = build_artifact_row("a.py", 10, "utility", "zo-ladder-low")
    c = json.loads(row["content"])
    assert c["built_at"]            # auto-stamped, non-empty
    assert c["model"] == "" and c["task"] == ""   # optional fields default empty
