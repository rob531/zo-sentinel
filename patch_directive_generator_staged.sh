#!/usr/bin/env bash
# SUPERSEDED 2026-04-17 -- do not run.
#
# v1 had a heredoc escaping bug in diversity_fn (return "\n".join was
# consumed by triple-single-quote parser, produced syntax error at line 187
# of the patched output). Replaced by patch_directive_generator_staged_v2.sh
# which uses chr(10) instead of "\n" to avoid the escape class entirely.
echo "Superseded. Use patch_directive_generator_staged_v2.sh instead."
exit 1