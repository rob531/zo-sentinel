"""tools/score_deterministic -- a GPU-free, deterministic replacement for the
vast SFT-student axis scorer.

It classifies the 7 MCP risk axes FROM A SERVER DESCRIPTION (the same input the
v3.0 student consumes) by posing each axis's rubric as a choice-classification to
a funded deterministic decider (GLiDE / jev), and emits rows identical in shape to
`mcp_llm_axis_scores` so the output feeds the canonical tier calc unchanged.

Modules:
  rubrics.py                -- the 7 axes + verbatim class sets + instructions
  det_scorer.py             -- per-server scorer + pluggable backend (glide|jev)
  det_validate.py           -- agreement of deterministic vs stored student labels
  score_deterministic_run.py-- batch over the Fly-PG never-scored backlog

FU-058. Add-only, staged.
"""
