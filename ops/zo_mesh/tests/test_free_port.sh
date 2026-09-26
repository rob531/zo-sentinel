#!/usr/bin/env bash
# Negative control for svc-free-the-port (GH #5580).
#
# R4: an assertion never observed RED is not evidence. POLE 1 reproduces the
# ACTUAL 2026-09-26 defect -- pkill the declared wrapper, watch the port stay
# held by its orphaned child -- and the test FAILS if that pole does not go red,
# because then the cure is answering a hazard that does not exist.
#
# Loads the functions from the SHIPPED watchdog.sh, never a copy: a test with
# its own copy of the code under test proves nothing about what runs.
#
#   bash test_free_port.sh [/path/to/watchdog.sh] [port]
set -u
WD=${1:-/home/workspace/zo_mesh/watchdog.sh}
PORT=${2:-18772}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

log() { echo "    [watchdog-log] $*"; }

eval "$(sed -n '/^_port_holders()/,/^}/p' "$WD")"
eval "$(sed -n '/^_free_port()/,/^}/p' "$WD")"
if ! type _free_port >/dev/null 2>&1 || ! type _port_holders >/dev/null 2>&1; then
    echo "FAIL: _free_port/_port_holders not found in $WD (unpatched?)"
    exit 2
fi

# watchdog_daemon.py invokes watchdog.sh with /bin/zsh, not bash. A green run
# under bash says nothing about the shell that actually runs it, so the run
# names its own interpreter rather than letting the reader assume one.
echo "shell under test: $(ps -o comm= -p $$ 2>/dev/null || echo unknown)"

fail=0

# A wrapper that spawns the real listener, exactly like write_service_wrapper.sh.
cat > "$TMP/fake_wrapper.sh" <<WRAP
#!/usr/bin/env bash
python3 -c "
import socket, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('127.0.0.1', $PORT))
s.listen(5)
time.sleep(600)
" &
wait
WRAP
chmod +x "$TMP/fake_wrapper.sh"

setsid bash "$TMP/fake_wrapper.sh" >/dev/null 2>&1 &
sleep 3

if [ -z "$(_port_holders "$PORT")" ]; then
    echo "SETUP FAIL: nothing listening on :$PORT -- cannot run the control"
    exit 2
fi
echo "setup: :$PORT held by pid(s) $(_port_holders "$PORT" | tr '\n' ' ')"

# ---- POLE 1: MUST GO RED. The old remedy leaves the port held. ----
pkill -f "fake_wrapper.sh" 2>/dev/null || true
sleep 2
if [ -n "$(_port_holders "$PORT")" ]; then
    echo "POLE1 RED (required): :$PORT STILL held after pkill-the-wrapper"
else
    echo "POLE1 FAIL: the wrapper pkill freed :$PORT -- premise of the cure is wrong"
    fail=1
fi

# ---- POLE 2: the cure frees it. ----
if _free_port "$PORT" "TestSvc" && [ -z "$(_port_holders "$PORT")" ]; then
    echo "POLE2 GREEN: :$PORT freed by _free_port"
else
    echo "POLE2 FAIL: :$PORT still held after _free_port"
    fail=1
fi

# ---- POLE 3: idempotent. A second call on a free port is an rc-0 no-op. ----
if _free_port "$PORT" "TestSvc"; then
    echo "POLE3 GREEN: idempotent rc-0 no-op on a free port"
else
    echo "POLE3 FAIL: _free_port non-zero on a free port"
    fail=1
fi

pkill -f "127.0.0.1', $PORT" 2>/dev/null || true
if [ "$fail" = "0" ]; then echo "ALL POLES PASS"; else echo "TEST FAILED"; fi
exit $fail
