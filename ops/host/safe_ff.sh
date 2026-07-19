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
#   3. stash tracked local modifications (tagged, recoverable) AND write the
#      stash out as a durable .patch archive (2026-07-19: stash stack had
#      silently grown 17 deep; entries are snapshots of recurring daemon
#      churn -- runtime state files + orphaned builder outputs -- so the
#      durable copy is the archive, not the stack)
#   4. prune archived auto-stash entries older than $SAFE_FF_STASH_KEEP_DAYS
#      (default 7) -- never prunes an entry whose patch archive is missing
#   5. git merge --ff-only origin/main; if untracked colliders STILL block it
#      (anything the step-2 scan missed, e.g. dir/file conflicts or rename
#      edge cases), MOVE each path git names (mv, never rm; path-preserving)
#      into $SAFE_FF_BACKUP_ROOT/<utc-ts>/colliders/ and retry exactly once
#      (FU-007). No-op on a clean clone: the first ff succeeds and the rescue
#      never fires. NOTE (2026-07-19): the runtime clone carries thousands of
#      untracked runtime-state files (directive queue, flag files, app db)
#      that daemons depend on -- so we deliberately move ONLY paths that
#      actually block the ff, never the whole untracked set.
#
# Exit: 0 ok (incl. already-up-to-date) | 2 env/fetch failure | 3 ff refused.
# Usage: bash ops/host/safe_ff.sh [repo_dir]
set -uo pipefail

REPO_DIR="${1:-/home/workspace/zo_sentinel}"
BACKUP_ROOT="${SAFE_FF_BACKUP_ROOT:-/home/workspace/zo_sentinel_state/refresh_backups}"
STASH_ARCHIVE="${SAFE_FF_STASH_ARCHIVE:-/home/workspace/zo_sentinel_state/stash_archive}"
KEEP_DAYS="${SAFE_FF_STASH_KEEP_DAYS:-7}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

cd "$REPO_DIR" || { echo "FATAL: cannot cd $REPO_DIR"; exit 2; }
git fetch origin main -q || { echo "FATAL: git fetch failed"; exit 2; }

if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ]; then
    echo "UP-TO-DATE: $(git rev-parse --short HEAD)"
    exit 0
fi

# --- 1. back up untracked files that the incoming tree will add ---------------
# --no-renames: keep renamed-in paths visible as plain adds so colliders at a
# rename destination are caught here too (FU-007).
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
done < <(git diff --name-only --no-renames --diff-filter=A HEAD origin/main)
[ "$moved" -gt 0 ] && echo "backup dir: $BACKUP_ROOT/$TS ($moved file(s))"

# --- 2. stash tracked local modifications + durable patch archive -------------
if ! git diff --quiet || ! git diff --cached --quiet; then
    git stash push -m "safe_ff auto-stash $TS" -q \
        && echo "STASHED tracked local modifications (git stash list to inspect)"
    mkdir -p "$STASH_ARCHIVE"
    git stash show -p "stash@{0}" > "$STASH_ARCHIVE/$TS.patch" 2>/dev/null \
        && echo "ARCHIVED stash patch: $STASH_ARCHIVE/$TS.patch"
fi

# --- 3. prune archived auto-stashes older than KEEP_DAYS ----------------------
CUTOFF="$(date -u -d "$KEEP_DAYS days ago" +%Y%m%dT%H%M%SZ 2>/dev/null || echo "")"
if [ -n "$CUTOFF" ]; then
    pruned=0
    # collect (index, ts) for auto-stash entries older than cutoff; drop from
    # the HIGHEST index down because dropping renumbers the stack
    while IFS=' ' read -r idx sts; do
        [ -n "$idx" ] || continue
        # durable copy must exist before we drop (write it now if missing)
        if [ ! -s "$STASH_ARCHIVE/$sts.patch" ]; then
            mkdir -p "$STASH_ARCHIVE"
            git stash show -p "stash@{$idx}" > "$STASH_ARCHIVE/$sts.patch" 2>/dev/null || continue
        fi
        git stash drop "stash@{$idx}" -q && pruned=$((pruned + 1))
    done < <(git stash list --format='%gs' \
             | awk -v cut="$CUTOFF" '{
                   line = NR - 1
                   ts = $0
                   if (sub(/.*safe_ff auto-stash /, "", ts) && ts < cut)
                       print line, ts
               }' | sort -rn)
    [ "$pruned" -gt 0 ] && echo "PRUNED $pruned auto-stash entr(ies) older than ${KEEP_DAYS}d (patches: $STASH_ARCHIVE)"
fi

# --- 4. fast-forward (with one-shot untracked-collider rescue, FU-007) --------
if ff_out="$(git merge --ff-only origin/main -q 2>&1)"; then
    echo "HEAD: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s | cut -c1-60)"
    exit 0
fi

# ff refused. If git names untracked files it would overwrite/remove, MOVE each
# one (mv, never rm; path-preserving) into $BACKUP_ROOT/$TS/colliders/ and
# retry exactly once. Anything else falls through to the existing FATAL path.
# Two error formats exist: a plural block (unpack-trees) and a singular
# one-line variant (read-tree codepath) -- parse both.
rescued=0
while IFS= read -r path; do
    [ -n "$path" ] || continue
    [ -e "$path" ] || continue
    # safety: never move a tracked path from here
    git ls-files --error-unmatch "$path" >/dev/null 2>&1 && continue
    dest="$BACKUP_ROOT/$TS/colliders/$path"
    mkdir -p "$(dirname "$dest")"
    mv "$path" "$dest" && rescued=$((rescued + 1)) \
        && echo "RESCUED untracked collider: $path"
done < <(printf '%s\n' "$ff_out" \
         | awk '/untracked working tree files would be (overwritten|removed) by/ {grab=1; next}
                /^(Please move or remove|Aborting)/ {grab=0}
                grab {sub(/^[ \t]+/, ""); print}'
         printf '%s\n' "$ff_out" \
         | sed -n "s/^error: Untracked working tree file '\(.*\)' would be [a-z]* by [a-z]*\.\$/\1/p")

if [ "$rescued" -gt 0 ]; then
    echo "collider dir: $BACKUP_ROOT/$TS/colliders ($rescued file(s)); retrying ff once"
    if git merge --ff-only origin/main -q; then
        echo "HEAD: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s | cut -c1-60)"
        exit 0
    fi
fi
printf '%s\n' "$ff_out"
echo "FATAL: ff refused even after backup+stash -- inspect manually (diverged history?)"
exit 3
