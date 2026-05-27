"""zo_sentinel.evaluators

Reverse-feed evaluators. Read external evaluation signals (e.g. GitHub
Actions check runs from the cheap Goose-T2 evaluator workflow) and
write them as memory_type='gh_check_failure' rows into mesh_memory so
the Directive Architect can pick them up via read_failure_history.
"""
