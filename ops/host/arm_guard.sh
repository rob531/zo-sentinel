#!/usr/bin/env bash
# arm_guard.sh -- give ARMING a trigger, so it stops waiting for a lane.
#
# WHY THIS FILE EXISTS
#   ops/host/safe_ff.sh is this fleet's arming mechanism. It fast-forwards the
#   runtime checkout to origin/main, moving (never deleting, path-preserving)
#   the untracked builder artifacts that otherwise make `git merge --ff-only`
#   refuse. It is correct, tracked, tested and non-destructive.
#
#   On 2026-09-30 a census found that NOTHING CALLS IT. Not cron, not
#   supervisor, not any code path. Its only trigger anywhere in the tree is a
#   PROSE STRING in sentinel_directive_generator_goose.py -- "DEPLOY FIRST
#   (safe_ff.sh)" -- a paragraph asking an agent to behave, which is precisely
#   the failure mode this fleet's first rule exists to replace. Measured on the
#   host that morning, before this file existed:
#
#     runtime checkout /home/workspace/zo_sentinel  ... 52 commits behind main
#     git merge --ff-only origin/main ............... REFUSED, 42 untracked colliders
#     _reject_phantom_columns in builder_mcp.py ..... 0   (FU-568, merged 09-29)
#     BAD_TOML in tools/generate_spine.py ........... 0   (FU-570, merged 09-30)
#
#   Two merged product cures -- one of them the emission gate chairman issue
#   #4080's columns half depends on -- were dead on the host, because the
#   arming step had no trigger. That is R2 of HARNESS_DOCTRINE.md ("a merge is
#   not an arming") with nothing behind it but a sentence.
#
# WHAT THIS DOES
#   For arming, exactly what tools/bus_catalog_guard.sh already does for the
#   bus snapshot. It adds NO judgement of its own: it decides only *whether
#   safe_ff.sh needs to run*. safe_ff.sh still owns every file decision, every
#   backup and every stash. Two triggers, one existing action.
#
#     on boot      -- catch drift accumulated while the box was powered off
#     on schedule  -- hourly; costs one fetch + two rev-parse when up to date
#     on demand    -- bash ops/host/arm_guard.sh [--dry-run]
#
# VISIBILITY -- the point of the file, not a nicety
#   EVERY run, INCLUDING the no-op, stamps $ARM_HEARTBEAT with commits_behind
#   before and after. Arming that stops is then visible as a heartbeat that
#   stopped moving, rather than as a drift each cycle re-derives by hand --
#   which is what happened: three separate cycles independently measured "~130
#   behind", "34 behind" and "52 behind" and none of them left a number the
#   next one could read. A heartbeat written only when work happens cannot
#   distinguish "healthy and idle" from "dead", so it is written on the no-op
#   path too.
#
# THIS IS NOT A GATE. It blocks nothing, fails no build, adds no required
#   check and cannot refuse a PR. It gives an existing repair a trigger. On an
#   already-current checkout it is a no-op that exits 0.
#
# Exit: 0 ok (including already-current) | 2 env/fetch failure | 3 ff refused
# Usage: bash ops/host/arm_guard.sh [--dry-run] [repo_dir]
set -uo pipefail

REPO="${ZO_REPO:-/home/workspace/zo_sentinel}"
HEARTBEAT="${ARM_HEARTBEAT:-/home/workspace/logs/arm_heartbeat.json}"
BOOT_SETTLE="${BOOT_SETTLE:-0}"
# Audit finding B2: fetch the action AS TRACKED ON main and run that, never a
# path into the build workspace. The working tree is routinely dozens of
# commits stale -- that is the very condition this guard exists to clear, so
# its own copy of safe_ff.sh is exactly the copy not to trust.
SAFE_FF_REF="${ZO_SAFE_FF_REF:-origin/main:ops/host/safe_ff.sh}"
DRY=0

for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    -*)        ;;
    *)         REPO="$a" ;;
  esac
done

log() { echo "[arm-guard] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

# Stamp the heartbeat. Called on EVERY exit path, including the no-op and both
# failure paths -- a heartbeat that only appears on success is a heartbeat that
# goes quiet exactly when you need it to speak.
stamp() {
  local before="$1" after="$2" acted="$3" reason="$4" rc="$5"
  mkdir -p "$(dirname "$HEARTBEAT")" 2>/dev/null
  cat > "$HEARTBEAT" <<JSON
{
  "last_run_at": "$(date -u +%Y-%m-%dT%H:%M:%S+00:00)",
  "repo": "$REPO",
  "commits_behind_before": $before,
  "commits_behind_after": $after,
  "head_after": "$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)",
  "action_taken": $acted,
  "reason": "$reason",
  "safe_ff_exit_code": $rc
}
JSON
}

[ "$BOOT_SETTLE" -gt 0 ] && { log "boot settle ${BOOT_SETTLE}s"; sleep "$BOOT_SETTLE"; }

if ! git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  log "FATAL: $REPO is not a git checkout"
  stamp -1 -1 false "repo_unreadable" 2
  exit 2
fi

if ! git -C "$REPO" fetch origin main -q 2>/dev/null; then
  log "FATAL: git fetch failed -- refusing to report a drift measured on stale refs"
  stamp -1 -1 false "fetch_failed" 2
  exit 2
fi

behind_before="$(git -C "$REPO" rev-list --count HEAD..origin/main 2>/dev/null || echo -1)"

if [ "$behind_before" = "0" ]; then
  log "UP-TO-DATE: $(git -C "$REPO" rev-parse --short HEAD) -- nothing to arm"
  stamp 0 0 false "already_current" 0
  exit 0
fi

log "BEHIND by $behind_before commit(s) -- arming via safe_ff.sh"

if [ "$DRY" = "1" ]; then
  log "DRY-RUN: would run safe_ff.sh ($SAFE_FF_REF) against $REPO"
  stamp "$behind_before" "$behind_before" false "dry_run" 0
  exit 0
fi

tmp_ff="$(mktemp)"
if ! git -C "$REPO" show "$SAFE_FF_REF" > "$tmp_ff" 2>/dev/null || [ ! -s "$tmp_ff" ]; then
  rm -f "$tmp_ff"
  log "FATAL: could not materialise $SAFE_FF_REF -- NOT falling back to the working-tree copy (B2)"
  stamp "$behind_before" "$behind_before" false "safe_ff_unresolvable" 2
  exit 2
fi

bash "$tmp_ff" "$REPO"
ff_rc=$?
rm -f "$tmp_ff"

behind_after="$(git -C "$REPO" rev-list --count HEAD..origin/main 2>/dev/null || echo -1)"

if [ "$behind_after" = "0" ]; then
  log "ARMED: $(git -C "$REPO" rev-parse --short HEAD) (was $behind_before behind)"
  stamp "$behind_before" 0 true "armed" "$ff_rc"
  exit 0
fi

# safe_ff.sh already moved every collider it could name and retried once. If we
# are still behind, say so loudly rather than exiting 0 on an unarmed host --
# reporting a merge as an arming is the defect this file is named after.
log "REFUSED: still $behind_after behind after safe_ff.sh (rc=$ff_rc) -- NOT armed"
stamp "$behind_before" "$behind_after" true "ff_refused" "$ff_rc"
exit 3
