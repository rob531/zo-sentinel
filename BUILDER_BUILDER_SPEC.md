# Builder-Builder (ZO-MetaBuilder) — Design Specification
## Version 0.1 | 2026-04-23

---

## 1. What It Is

A meta-service that makes the builder smarter over time and ready to build
anything — not just ZO-Sentinel modules. Operates at two levels:

**Level 1 — Self-improvement:** Watches builder outputs, failures, and model
behavior. Autonomously improves the knowledge base, directive schemas, and
antipattern lists. Proposes (but does not apply) patches to builder code itself.

**Level 2 — Generalization:** Manages a project anchor system so the builder
can be directed at any target project with its own DB schema, wiring rules,
and build context. Today it's hardcoded for Sentinel. With anchors it becomes
a general-purpose autonomous code builder.

---

## 2. Boundary with daily_check.py

```
daily_check.py          -- operational ping (is everything running?)
                           runs at 07:00 on ZoComputer, exit code 0/1/2

tower_orchestrator.py   -- strategic driver (is the system getting better?)
                           calls daily_check.py, acts on exit code,
                           then triggers builder-builder analysis pass

builder_builder.py      -- the analysis engine itself
                           called by tower_orchestrator, not by cron directly
```

No overlap. Tower calls ZoComputer daily_check, observes health, then runs
builder-builder as a separate analysis pass.

---

## 3. What Builder-Builder Watches

### Input Sources

| Source | What it reveals |
|--------|-----------------|
| `GENERATION_FAILURES.md` | Which tasks fail repeatedly, failure patterns |
| `BUILD_MANIFEST.md` | Smoke_fail rate by phase, complexity, model |
| `.build_registry.json` | Status distribution, which files are hollow stubs |
| `KNOWLEDGE_BASE.md` | Current wiring rules (what's already known) |
| `SENTINEL_DIRECTIVE_SCHEMA.md` | Current directive guidance |
| `mesh_memory` escalation_call records | Which models perform well/poorly |
| `builder log` tail | Recurring error signatures, timeout patterns |
| `DB_SCHEMA.md` vs `KNOWLEDGE_BASE.md` | Column name drift |
| `directives/*.done.json` | Description quality that led to good builds |

### Analysis Questions
1. Are the same column names being generated wrong repeatedly?
   → Update KNOWLEDGE_BASE.md "Common Mistakes" section
2. Is a particular complexity tier (high/medium/low) failing more than 30%?
   → Propose directive description improvements for that tier
3. Are there new error signatures appearing in smoke failures?
   → Add to SMOKE_ANTIPATTERNS in builder source
4. Which model is producing the best code per rung?
   → Update escalation LADDER ordering recommendation
5. Are there new tables in DB_SCHEMA.md not reflected in KNOWLEDGE_BASE.md?
   → Sync the knowledge base
6. Are there task descriptions that keep producing stubs (<500 bytes)?
   → Rewrite those descriptions with more specificity
7. Are any import packages failing repeatedly?
   → Add to known-bad package list, suggest alternatives

---

## 4. What It Produces

### Autonomous outputs (no human review needed)
- Updated `KNOWLEDGE_BASE.md` — new wiring rules, column corrections
- Updated `SENTINEL_DIRECTIVE_SCHEMA.md` — improved descriptions, new gotchas
- Updated `DB_SCHEMA.md` — sync from live information_schema
- New entries in `SMOKE_ANTIPATTERNS` list (append only, never remove)
- `BUILDER_HEALTH_REPORT.md` — weekly summary of build quality trends

### Gated outputs (written to pending/, human reviews before apply)
- `builder_patches/pending/patch_YYYYMMDD_<reason>.py` — proposed changes
  to zo_sentinel_builder.py itself
- `builder_patches/pending/ladder_YYYYMMDD.json` — proposed escalation
  LADDER reordering based on observed model performance

### Gate mechanism
```
zm review-patches      -- shows pending patches with diffs
zm apply-patch <name>  -- moves from pending/ to applied/, copies to target
zm reject-patch <name> -- moves to rejected/ with reason
```

---

## 5. Project Anchor System (Generalization)

Makes builder target-agnostic. Each project has an anchor directory:

```
/home/workspace/zo_builder_anchors/
  sentinel/
    PROJECT_CONTEXT.md      -- what the project is, tech stack
    DB_SCHEMA.md            -- live-generated from information_schema
    WIRING_RULES.md         -- project-specific antipatterns
    ALREADY_BUILT.md        -- list of existing files (don't regenerate)
    DIRECTIVE_SCHEMA.md     -- directive format for this project
  soc2_agent/
    PROJECT_CONTEXT.md
    DB_SCHEMA.md
    WIRING_RULES.md
    ...
  cert_tool/
    ...
```

Directives gain a `project` field:
```json
{
  "task": "build_control_mapper",
  "project": "soc2_agent",   // <-- builder loads anchors/soc2_agent/
  "handler": "generate_file",
  ...
}
```

Builder reads the right anchor set based on `project`. Defaults to `sentinel`
for backward compatibility. Builder-builder manages anchor freshness for each
registered project.

---

## 6. Library + Platform Research

When a directive references a package that has failed to import 3+ times,
builder-builder:
1. Checks if package exists in PyPI (via requests to pypi.org API)
2. Checks if there's a version incompatibility with Python 3.11
3. Looks up the package's ZoComputer availability
4. Proposes an alternative if the package is unavailable
5. Adds a note to KNOWLEDGE_BASE.md: "avoid X, use Y instead"

For new DB platforms (e.g. when a directive targets PostgreSQL or Redis
instead of DuckDB), builder-builder generates a new WIRING_RULES.md section
for that backend and adds it to the relevant project anchor.

---

## 7. Implementation Plan

### Phase 1 — Analysis engine (build first)
`builder_builder.py` — standalone script, no daemon
- Reads all input sources
- Produces BUILDER_HEALTH_REPORT.md
- Autonomously updates KNOWLEDGE_BASE.md with new column corrections
- Run manually: `python3 builder_builder.py`
- Run by tower: called from tower_orchestrator.py after daily_check passes

### Phase 2 — Patch proposals
- Adds patch writing for gated outputs
- `builder_patches/` directory structure
- `zm review-patches` / `zm apply-patch` added to zm_extra.zsh

### Phase 3 — Anchor system
- Project anchor directories
- Builder reads `project` field from directives
- Builder-builder manages anchor freshness
- First non-Sentinel project: soc2_agent

### Phase 4 — Tower integration
- `tower_orchestrator.py` calls daily_check then builder_builder
- Scheduled overnight analysis pass on tower compute
- Results pushed back to ZoComputer via MCP

---

## 8. Key Constraint

Builder-builder NEVER modifies `zo_sentinel_builder.py` autonomously.
Code patches always go through the pending/ gate. This preserves the
invariant that a human has reviewed every change to the build engine itself.
Knowledge files (KNOWLEDGE_BASE.md, DIRECTIVE_SCHEMA.md) are safe to
update autonomously because they affect prompt quality, not control flow.

---

## 9. File Locations

```
/home/workspace/zo_sentinel/builder_builder.py     -- main analysis engine
/home/workspace/zo_sentinel/builder_patches/
  pending/    -- proposed code patches awaiting review
  applied/    -- approved and deployed patches
  rejected/   -- declined patches with reasons
/home/workspace/zo_builder_anchors/                -- project anchor system
  sentinel/   -- current project (migrated from zo_sentinel/)
  soc2_agent/ -- next project
```