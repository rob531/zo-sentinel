#!/usr/bin/env bash
# safe_ff.sh -- non-destructive fast-forward of the runtime checkout to origin/main.
#
# WHY. Builder daemons drop UNTRACKED artifacts (e.g. breaker_actions/
# breaker_action_investigate_*.md) into the working tree, and the SAME paths
# later arrive TRACKED via merged PRs. `git merge --ff-only` then refuses with
# "untracked working tree files would be overwritten". This blocked the deploy
# heartbeat on 2026-07-02 and again 2026-07-06; both times the fix was a manual
# path-preserving backup. This script makes that backup durable + machine-run.
#
# WHAT IT DOES
#   1. git fetch origin main
#   2. diff HEAD..origin/main for ADDED paths that exist locally but are
#      untracked -> move each (path-preserving) into
#      $SAFE_FF_BACKUP_ROOT/<utc-ts>/ (default lives in zo_sentinel_state/,
#      OUTSIDE the repo, so `git clean` on daemon respawn can't eat it)
#   3. stash tracked local modifications (tagged, recoverable)
#   4. git merge --ff-only origin/main
#
# Exit: 0 ok (incl. already-up-to-date) | 2 env/fetch failure | 3 ff refused.
# Usage: bash ops/host/safe_ff.sh [repo_dir]
set -uo pipefail

REPO_DIR="${1:-/home/workspace/zo_sentinel}"
BACKUP_ROOT="${SAFE_FF_BACKUP_ROOT:-/home/workspace/zo_sentinel_state/refresh_backups}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

cd "$REPO_DIR" || { echo "FATAL: cannot cd $REPO_DIR"; exit 2; }
git fetch origin main -q || { echo "FATAL: git fetch failed"; exit 2; }

if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
    echo "UP-TO-DATE: $(git rev-parse --short HEAD)"
    exit 0
fi

# --- 1. back up untracked files that the incoming tree will add ---------------
moved=0
while IFS= read -r path; do
    [ -n "$path" ] || continue
    [ -e "$path" ] || continue
    if ! git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
        dest="$BACKUP_ROOT/$TS/$path"
        mkdir -p "$(dirname "$dest")"
        mv "$path" "$dest" && moved=$((moved + 1)) \
            && echo "BACKED-UP untracked collider: $path"
    fi
done < <(git diff --name-only --diff-filter=A HEAD origin/main)
[ "$moved" -gt 0 ] && echo "backup dir: $BACKUP_ROOT/$TS ($moved file(s))"

# --- 2. stash tracked local modifications (never lose local work) -------------
if ! git diff --quiet || ! git diff --cached --quiet; then
    git stash push -m "safe_ff auto-stash $TS" -q \
        && echo "STASHED tracked local modifications (git stash list to inspect)"
fi

# --- 3. fast-forward -----------------------------------------------------------
if git merge --ff-only origin/main -q; then
    echo "HEAD: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s | cut -c1-60)"
    exit 0
fi
echo "FATAL: ff refused even after backup+stash -- inspect manually (diverged history?)"
exit 3
