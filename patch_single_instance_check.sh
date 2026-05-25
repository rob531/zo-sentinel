#!/usr/bin/env bash
# patch_single_instance_check.sh
# ------------------------------------------------------------------------------
# Replaces the broken pgrep-based check_single_instance() in:
#   - trust_synthesiser.py
#   - risk_ranker.py
# with an fcntl.flock-based lockfile pattern. Same semantics as
# pi_corpus_ingest.py, which uses it correctly.
#
# The pgrep pattern is broken because:
#   - pgrep -f "trust_synthesiser.py" matches ANY process whose command
#     line contains that string -- including shells tailing the log file,
#     editors with the file open, grep/cat/less operations, etc.
#   - The check was seeing count=2 because your 'tail -f sentinel_trust_synthesiser.log'
#     was itself matching the pgrep pattern (the log filename contains the
#     daemon's process name).
#
# The fcntl.flock pattern works because:
#   - Exclusive non-blocking lock on a dedicated file (/tmp/<name>.lock)
#   - Kernel-enforced, so diagnostic commands can't accidentally collide
#   - Lock released automatically on process exit (no stale PID files)
#   - Same pattern the well-behaved pi_corpus_ingest.py already uses
#
# Safety:
#   - Backs up each file to .bak.<timestamp> before editing
#   - ast.parse validation after each edit -- rolls back on syntax error
#   - Dry-run mode: DRY_RUN=1 bash patch_single_instance_check.sh
#   - Idempotent: if already patched (looks for 'fcntl.flock' marker), skips
#
# Usage:
#   DRY_RUN=1 bash /home/workspace/zo_sentinel/patch_single_instance_check.sh
#   bash /home/workspace/zo_sentinel/patch_single_instance_check.sh
#   SKIP_RESTART=1 bash /home/workspace/zo_sentinel/patch_single_instance_check.sh
# ------------------------------------------------------------------------------

set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
DRY_RUN="${DRY_RUN:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'; CYA=$'\033[96m'; DIM=$'\033[2m'; BLD=$'\033[1m'; NC=$'\033[0m'
h1()   { printf "\n%s%s=== %s ===%s\n" "$BLD" "$CYA" "$*" "$NC"; }
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
dry()  { [[ "$DRY_RUN" == "1" ]] && printf "  %s[DRY]%s would: %s\n" "$DIM" "$NC" "$*"; }

FILES=(
    "trust_synthesiser.py:trust_synthesiser:CYCLE_INTERVAL"
    "risk_ranker.py:risk_ranker:POLL_INTERVAL"
)

h1 "Single-instance-check patcher"
printf "timestamp: %s  dry-run: %s\n" "$TS" "$DRY_RUN"

# Ensure write_service is up -- daemons need it for heartbeats
WS="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null || echo 000)"
[[ "$WS" == "200" ]] && ok "write_service :8772 healthy" || { bad "write_service :8772 returned $WS -- restart mesh first"; exit 2; }

# ------------------------------------------------------------------------------
# Patch each file
# ------------------------------------------------------------------------------
for entry in "${FILES[@]}"; do
    fname="${entry%%:*}"
    svc="$(echo "$entry" | cut -d: -f2)"
    path="$SENTINEL/$fname"

    h1 "$fname"

    [[ -f "$path" ]] || { bad "$path MISSING"; continue; }

    python3 -c "import ast; ast.parse(open('$path').read())" 2>/dev/null \
        && ok "pre-flight: parses cleanly" \
        || { bad "pre-flight: $path already has syntax errors -- skipping"; continue; }

    # Idempotency check
    if grep -q 'fcntl.flock' "$path"; then
        ok "Already patched (fcntl.flock present) -- skipping"
        continue
    fi
    if ! grep -q 'check_single_instance' "$path"; then
        warn "No check_single_instance found -- nothing to patch, skipping"
        continue
    fi

    # Backup
    if [[ "$DRY_RUN" == "0" ]]; then
        cp "$path" "$path.bak.$TS"
        ok "Backup -> $path.bak.$TS"
    else
        dry "cp $path $path.bak.$TS"
    fi

    # AST-safe Python rewriter -- handles two source shapes:
    #   trust_synthesiser.py:  def check_single_instance(proc_name: str) -> bool:  (returns True=OK)
    #   risk_ranker.py:        def check_single_instance() -> bool:                 (returns True=EXIT)
    # Both call sites are different too. The rewriter replaces:
    #   (a) the function body with an fcntl.flock lock acquisition
    #   (b) changes the semantics so 'function returns None on success, sys.exit(1) on failure'
    #   (c) the caller site to just 'check_single_instance()' (no if-wrap)
    REWRITER="$(mktemp)"
    cat > "$REWRITER" <<'PYEOF'
import ast, re, sys

path = sys.argv[1]
service = sys.argv[2]
src = open(path).read()

# 1. Ensure 'import fcntl' is present.
if 'import fcntl' not in src:
    # insert after the last top-level 'import ' or 'from ' line in the first block
    m = re.search(r'(^(?:from|import) [^\n]+\n)(?!(?:from|import) )', src, re.MULTILINE)
    if m:
        src = src[:m.end()] + 'import fcntl\n' + src[m.end():]
    else:
        # fallback: add after the first 'import os' we find
        src = re.sub(r'^(import os\n)', r'\1import fcntl\n', src, count=1, flags=re.MULTILINE)

# 2. Replace the check_single_instance function with a flock-based version.
#    Match from 'def check_single_instance' through the closing 'return False' or 'return True'.
new_fn = f'''def check_single_instance():
    """Acquire exclusive flock on /tmp/{service}.lock. Exit on collision.

    Replaces the previous pgrep-based check which produced false positives
    whenever ANY other process had the script name in its command line
    (tail -f on the log, editors, grep, etc.). The flock is kernel-enforced
    and released automatically on process exit -- no stale PID files.
    Returned lock-file fd is kept alive by module-level reference.
    """
    lock_path = '/tmp/{service}.lock'
    try:
        fd = open(lock_path, 'w')
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(__import__("os").getpid()))
        fd.flush()
        globals()['_single_instance_lock_fd'] = fd
        return True
    except (IOError, OSError):
        # Another instance holds the lock -- exit immediately.
        print(f"[{service}] Another instance holds lock at {{lock_path}} -- exiting", flush=True)
        sys.exit(0)
'''

# Replace existing function regardless of its signature/body
pattern = re.compile(
    r'def check_single_instance\([^)]*\)[^:]*:\n(?:    [^\n]*\n|    \n|\n)+',
    re.MULTILINE
)
if not pattern.search(src):
    print('ERROR: could not locate check_single_instance body', file=sys.stderr)
    sys.exit(3)

src = pattern.sub(new_fn + '\n\n', src, count=1)

# 3. Normalise call sites. Two known shapes:
#    (a) trust_synthesiser.py:  if not check_single_instance(proc_name):
#                                  logger.error(...)
#                                  sys.exit(1)
#    (b) risk_ranker.py:        if check_single_instance():
#                                  log.warning("Single instance check failed, exiting")
#                                  sys.exit(1)
#    Both become just 'check_single_instance()' -- the new function exits on its own.

# Pattern (a): if not check_single_instance(<anything>): (+3 lines of body)
src = re.sub(
    r'if not check_single_instance\([^)]*\):\n(?:    [^\n]+\n){1,3}',
    'check_single_instance()\n',
    src
)
# Pattern (b): if check_single_instance(): (+ up to 3 lines)
src = re.sub(
    r'if check_single_instance\(\):\n(?:    [^\n]+\n){1,3}',
    'check_single_instance()\n',
    src
)

# 4. Validate
try:
    ast.parse(src)
except SyntaxError as e:
    print(f'ERROR: patched source has syntax error: {e}', file=sys.stderr)
    sys.exit(4)

open(path, 'w').write(src)
print(f'ok: wrote {len(src)} bytes')
PYEOF

    if [[ "$DRY_RUN" == "1" ]]; then
        dry "rewrite $path with flock-based check_single_instance"
        rm -f "$REWRITER"
    else
        if python3 "$REWRITER" "$path" "$svc"; then
            python3 -c "import ast; ast.parse(open('$path').read())" 2>/dev/null \
                && ok "post-patch: parses cleanly" \
                || {
                    bad "post-patch: parse failed -- rolling back"
                    cp "$path.bak.$TS" "$path"
                    rm -f "$REWRITER"
                    continue
                }
            ok "Patched $fname"
        else
            bad "Rewriter failed -- rolling back"
            cp "$path.bak.$TS" "$path" 2>/dev/null
        fi
        rm -f "$REWRITER"
    fi
done

# ------------------------------------------------------------------------------
# Clean up stale processes + lockfiles, then restart
# ------------------------------------------------------------------------------
if [[ "$DRY_RUN" == "1" ]]; then
    h1 "Dry-run complete"
    ok "Run without DRY_RUN=1 to apply."
    exit 0
fi

if [[ "$SKIP_RESTART" == "1" ]]; then
    h1 "Skipping restart (SKIP_RESTART=1)"
    warn "Restart manually:"
    echo "    pkill -9 -f trust_synthesiser.py; pkill -9 -f risk_ranker.py"
    echo "    rm -f /tmp/trust_synthesiser.lock /tmp/risk_ranker.lock"
    echo "    sleep 2"
    echo "    nohup python3 $SENTINEL/trust_synthesiser.py >> $LOGS/sentinel_trust_synthesiser.log 2>&1 &"
    echo "    nohup python3 $SENTINEL/risk_ranker.py >> $LOGS/sentinel_risk_ranker.log 2>&1 &"
    exit 0
fi

h1 "Kill + restart"

for name in trust_synthesiser risk_ranker; do
    if pgrep -f "python3 .*${name}.py" >/dev/null 2>&1; then
        pkill -9 -f "python3 .*${name}.py" 2>/dev/null
        warn "killed $name"
    fi
    rm -f "/tmp/${name}.lock" 2>/dev/null
done
sleep 3

for name in trust_synthesiser risk_ranker; do
    # setsid detaches from tty; <&- closes stdin cleanly
    setsid python3 "$SENTINEL/${name}.py" >> "$LOGS/sentinel_${name}.log" 2>&1 <&- &
    sleep 3
    pid="$(pgrep -f "python3 .*${name}.py" 2>/dev/null | head -1)"
    if [[ -n "$pid" ]]; then
        ok "$name PID $pid"
    else
        bad "$name failed to start -- last 5 lines of log:"
        tail -5 "$LOGS/sentinel_${name}.log" | sed 's/^/    /'
    fi
done

# ------------------------------------------------------------------------------
# Verify heartbeats
# ------------------------------------------------------------------------------
h1 "Verify"
echo "Waiting 20s for first heartbeats..."
sleep 20

for name in trust_synthesiser risk_ranker; do
    RESP="$(curl -s -X POST http://127.0.0.1:8772/query \
        -H 'Content-Type: application/json' \
        --data-raw "{\"sql\":\"SELECT CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) AS age FROM service_health WHERE service='$name'\"}" 2>/dev/null)"
    AGE="$(echo "$RESP" | grep -oE '"age":[0-9]+' | head -1 | grep -oE '[0-9]+')"
    if [[ -n "$AGE" && "$AGE" -lt 60 ]]; then
        ok "$name heartbeat ${AGE}s ago"
    else
        warn "$name no fresh heartbeat -- check $LOGS/sentinel_${name}.log"
    fi
done

h1 "Done"
echo "Backups:"
ls -la "$SENTINEL"/*.bak.$TS 2>/dev/null | sed 's/^/  /'
echo
echo "Rollback commands if needed:"
echo "  cp $SENTINEL/trust_synthesiser.py.bak.$TS $SENTINEL/trust_synthesiser.py"
echo "  cp $SENTINEL/risk_ranker.py.bak.$TS       $SENTINEL/risk_ranker.py"
echo "  pkill -9 -f trust_synthesiser.py; pkill -9 -f risk_ranker.py"
echo "  rm -f /tmp/trust_synthesiser.lock /tmp/risk_ranker.lock"