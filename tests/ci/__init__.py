# tests.ci -- hermetic, CI-runnable smoke gates for zo-sentinel.
#
# The gates under tests/gates/ are ZoComputer-bound by design: they hash
# /home/workspace protected files and query live daemon state. This package
# is the GitHub-side counterpart -- a recursive (short-circuit) ladder of
# hermetic checks that run on a stock GitHub Actions runner against a mock
# write_service + an ephemeral DuckDB, with NO dependency on the live host.
#
# Entry point: python -m tests.ci.run_ci_smoke
