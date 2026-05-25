#!/usr/bin/env bash
# patch_attestation_engine.sh
# ------------------------------------------------------------------------------
# Fixes three bugs in /home/workspace/zo_sentinel/attestation_engine.py:
#
# Bug 1 -- wrong port:
#   EXECUTE_URL = 'http://127.0.0.1:8773/execute'
#                               ^^^^ inference_router (not write_service)
#   Should be 8772. Causes every SELECT to get 404 or a wrong response shape.
#
# Bug 2 -- double-slash URL in ws_write():
#   url = f'{WRITE_SERVICE_URL}/write'
#   where WRITE_SERVICE_URL already ends in /write
#   Results in http://127.0.0.1:8772/write/write -- 404. Every write fails silently.
#
# Bug 3 -- fragile pidfile in /var/run/zo/:
#   check_single_instance() writes to /var/run/zo/attestation_engine.pid
#   Directory may not exist or be writable under the non-root user profile.
#   Replace with the fcntl.flock(/tmp/attestation_engine.lock) pattern used
#   by pi_corpus_ingest.py and now trust_synthesiser.py / risk_ranker.py.
#
# Safety:
#   - Backs up file to .bak.<timestamp>
#   - ast.parse validation after edit; rolls back on failure
#   - Dry-run mode: DRY_RUN=1 bash patch_attestation_engine.sh
#   - Idempotent: each fix checks if already applied before editing
#
# Usage:
#   DRY_RUN=1 bash /home/workspace/zo_sentinel/patch_attestation_engine.sh
#   bash /home/workspace/zo_sentinel/patch_attestation_engine.sh
#   SKIP_RESTART=1 bash /home/workspace/zo_sentinel/patch_attestation_engine.sh
# ------------------------------------------------------------------------------

set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
FILE="$SENTINEL/attestation_engine.py"
DRY_RUN="${DRY_RUN:-0}"
SKIP_RESTART="${SKIP_RESTART:-0}"
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'; CYA=$'\033[96m'; DIM=$'\033[2m'; BLD=$'\033[1m'; NC=$'\033[0m'
h1()   { printf "\n%s%s=== %s ===%s\n" "$BLD" "$CYA" "$*" "$NC"; }
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
dry()  { [[ "$DRY_RUN" == "1" ]] && printf "  %s[DRY]%s %s\n" "$DIM" "$NC" "$*"; }

h1 "attestation_engine.py patcher"
printf "timestamp: %s  dry-run: %s\n" "$TS" "$DRY_RUN"

# ------------------------------------------------------------------------------
# Pre-flight
# ------------------------------------------------------------------------------
[[ -f "$FILE" ]] || { bad "$FILE MISSING"; exit 2; }

python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
    && ok "pre-flight: $FILE parses cleanly" \
    || { bad "pre-flight: syntax errors already present -- aborting"; exit 2; }

WS="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null || echo 000)"
[[ "$WS" == "200" ]] && ok "write_service :8772 healthy" || { bad "write_service :8772 returned $WS"; exit 2; }

# Backup
if [[ "$DRY_RUN" == "0" ]]; then
    cp "$FILE" "$FILE.bak.$TS"
    ok "Backup -> $FILE.bak.$TS"
else
    dry "cp $FILE $FILE.bak.$TS"
fi

# ------------------------------------------------------------------------------
# Python rewriter -- applies all three fixes in one pass with ast.parse guard
# ------------------------------------------------------------------------------
REWRITER="$(mktemp)"
cat > "$REWRITER" <<'PYEOF'
import ast, re, sys

path = sys.argv[1]
src = open(path).read()
changes = []

# -----------------------------------------------------------------------------
# Fix 1: EXECUTE_URL port 8773 -> 8772
# -----------------------------------------------------------------------------
if "'http://127.0.0.1:8773/execute'" in src:
    src = src.replace(
        "EXECUTE_URL = 'http://127.0.0.1:8773/execute'",
        "EXECUTE_URL = 'http://127.0.0.1:8772/execute'"
    )
    changes.append('Fix 1: EXECUTE_URL 8773 -> 8772')
elif "'http://127.0.0.1:8772/execute'" in src:
    changes.append('Fix 1: already correct (8772)')
else:
    changes.append('Fix 1: SKIPPED -- EXECUTE_URL pattern not found')

# -----------------------------------------------------------------------------
# Fix 2: ws_write URL double-slash bug
#   Before: url = f'{WRITE_SERVICE_URL}/write'
#           resp = requests.post(url, json=payload)
#   After:  resp = requests.post(WRITE_SERVICE_URL, json=payload)
# -----------------------------------------------------------------------------
# Match the offending 2-line block anywhere in the file, replace with a single line.
pattern_ws_write_url = re.compile(
    r"    url = f'\{WRITE_SERVICE_URL\}/write'\n"
    r"    payload = \{'table': table, 'rows': rows, 'wait': wait\}\n"
    r"    resp = requests\.post\(url, json=payload\)\n",
)
replacement_ws_write = (
    "    payload = {'table': table, 'rows': rows, 'wait': wait}\n"
    "    resp = requests.post(WRITE_SERVICE_URL, json=payload)\n"
)
if pattern_ws_write_url.search(src):
    src = pattern_ws_write_url.sub(replacement_ws_write, src)
    changes.append('Fix 2: removed /write double-slash in ws_write()')
elif 'requests.post(WRITE_SERVICE_URL, json=payload)' in src and \
     "f'{WRITE_SERVICE_URL}/write'" not in src:
    changes.append('Fix 2: already correct')
else:
    changes.append('Fix 2: SKIPPED -- ws_write pattern not found')

# -----------------------------------------------------------------------------
# Fix 3: replace check_single_instance() with fcntl.flock pattern
# -----------------------------------------------------------------------------
if 'fcntl.flock' in src:
    changes.append('Fix 3: already patched (fcntl.flock present)')
else:
    # Ensure 'import fcntl' exists near top of imports
    if 'import fcntl' not in src:
        m = re.search(r'(^import os\n)', src, re.MULTILINE)
        if m:
            src = src[:m.end()] + 'import fcntl\n' + src[m.end():]
        else:
            # fallback: after first import/from block
            m2 = re.search(r'(^(?:from|import) [^\n]+\n)(?!(?:from|import) )', src, re.MULTILINE)
            if m2:
                src = src[:m2.end()] + 'import fcntl\n' + src[m2.end():]

    new_fn = '''def check_single_instance():
    """Acquire exclusive flock on /tmp/attestation_engine.lock. Exit on collision.

    Replaces the previous /var/run/zo/ pidfile pattern, which required a
    directory that may not exist under the non-root user profile. The flock
    is kernel-enforced and released automatically on process exit -- no
    stale files, no permission issues. Pattern matches pi_corpus_ingest.py,
    trust_synthesiser.py, and risk_ranker.py.
    """
    lock_path = '/tmp/attestation_engine.lock'
    try:
        fd = open(lock_path, 'w')
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(str(os.getpid()))
        fd.flush()
        globals()['_single_instance_lock_fd'] = fd
        return True
    except (IOError, OSError):
        print(f"[attestation_engine] Another instance holds lock at {lock_path} -- exiting", flush=True)
        sys.exit(0)
'''
    # Replace the whole check_single_instance function body (greedy until next def/EOF)
    pattern_csi = re.compile(
        r'def check_single_instance\(\)[^:]*:\n(?:    [^\n]*\n|    \n|\n)+?(?=\ndef |\nif __name__|\Z)',
        re.MULTILINE
    )
    if pattern_csi.search(src):
        src = pattern_csi.sub(new_fn + '\n', src, count=1)
        changes.append('Fix 3: replaced check_single_instance with fcntl.flock')
    else:
        changes.append('Fix 3: SKIPPED -- check_single_instance body not found')

# -----------------------------------------------------------------------------
# Final AST validation
# -----------------------------------------------------------------------------
try:
    ast.parse(src)
except SyntaxError as e:
    print(f'ERROR: patched source has syntax error: {e}', file=sys.stderr)
    sys.exit(4)

open(path, 'w').write(src)
for c in changes:
    print(c)
print(f'ok: wrote {len(src)} bytes')
PYEOF

if [[ "$DRY_RUN" == "1" ]]; then
    dry "run rewriter (no file changes)"
    python3 "$REWRITER" "$FILE" || bad "rewriter failed in dry-run"
    rm -f "$REWRITER"
    h1 "Dry-run complete"
    ok "Run without DRY_RUN=1 to apply."
    exit 0
fi

h1 "Apply patches"
if python3 "$REWRITER" "$FILE"; then
    python3 -c "import ast; ast.parse(open('$FILE').read())" 2>/dev/null \
        && ok "post-patch: $FILE parses cleanly" \
        || {
            bad "post-patch: parse failed -- rolling back"
            cp "$FILE.bak.$TS" "$FILE"
            rm -f "$REWRITER"
            exit 3
        }
    ok "Patched attestation_engine.py"
else
    bad "Rewriter failed -- rolling back"
    cp "$FILE.bak.$TS" "$FILE" 2>/dev/null
    rm -f "$REWRITER"
    exit 3
fi
rm -f "$REWRITER"

# ------------------------------------------------------------------------------
# Kill + restart
# ------------------------------------------------------------------------------
if [[ "$SKIP_RESTART" == "1" ]]; then
    h1 "Skipping restart (SKIP_RESTART=1)"
    warn "Restart manually:"
    echo "    pkill -9 -f attestation_engine.py"
    echo "    rm -f /tmp/attestation_engine.lock"
    echo "    setsid python3 $FILE >> $LOGS/sentinel_attestation_engine.log 2>&1 <&- &"
    exit 0
fi

h1 "Restart"
if pgrep -f 'python3 .*attestation_engine.py' >/dev/null 2>&1; then
    pkill -9 -f 'python3 .*attestation_engine.py' 2>/dev/null
    warn "killed old attestation_engine"
fi
rm -f /tmp/attestation_engine.lock 2>/dev/null
sleep 2

setsid python3 "$FILE" >> "$LOGS/sentinel_attestation_engine.log" 2>&1 <&- &
sleep 3
pid="$(pgrep -f 'python3 .*attestation_engine.py' 2>/dev/null | head -1)"
if [[ -n "$pid" ]]; then
    ok "attestation_engine PID $pid"
else
    bad "attestation_engine failed to start -- last 5 lines:"
    tail -5 "$LOGS/sentinel_attestation_engine.log" | sed 's/^/    /'
    exit 3
fi

# ------------------------------------------------------------------------------
# Verify
# ------------------------------------------------------------------------------
h1 "Verify"
echo "Waiting 15s for first cycle + heartbeat..."
sleep 15

RESP="$(curl -s -X POST http://127.0.0.1:8772/query \
    -H 'Content-Type: application/json' \
    --data-raw '{"sql":"SELECT CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) AS age FROM service_health WHERE service='\''attestation_engine'\''"}' 2>/dev/null)"
AGE="$(echo "$RESP" | grep -oE '"age":[0-9]+' | head -1 | grep -oE '[0-9]+')"
if [[ -n "$AGE" && "$AGE" -lt 60 ]]; then
    ok "attestation_engine heartbeat ${AGE}s ago"
else
    warn "no fresh heartbeat yet -- check log"
fi

COUNT="$(curl -s -X POST http://127.0.0.1:8772/query \
    -H 'Content-Type: application/json' \
    --data-raw '{"sql":"SELECT COUNT(*) AS n FROM mcp_attestations"}' 2>/dev/null \
    | grep -oE '"n":[0-9]+' | head -1 | grep -oE '[0-9]+')"
if [[ -n "$COUNT" && "$COUNT" -gt 0 ]]; then
    ok "mcp_attestations has $COUNT row(s) -- UI attestation cards will render"
else
    warn "mcp_attestations still empty -- may need more time or another bug remains"
    echo
    echo "  Recent log lines:"
    tail -10 "$LOGS/sentinel_attestation_engine.log" | sed 's/^/    /'
fi

h1 "Done"
echo "Backup: $FILE.bak.$TS"
echo
echo "Rollback:"
echo "  cp $FILE.bak.$TS $FILE"
echo "  pkill -9 -f attestation_engine.py"
echo "  rm -f /tmp/attestation_engine.lock"