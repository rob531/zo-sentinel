#!/usr/bin/env bash
# Regression test for the go.sh ERR-trap subshell deadlock.
#
# Reproduces the exact shape of the bug: `set -e` + `set -E` + an ERR trap that
# ends in `exec sleep infinity`, with a failing command inside a command
# substitution (curl to a dead port -> rc 7).
#
#   OLD trap  -> parent blocks forever reading the substitution pipe (timeout).
#   NEW trap  -> substitution closes, script completes.
#
# Exit 0 only if OLD hangs AND NEW completes -- i.e. the test itself proves it
# is testing the right thing, not just that something passes.

DEAD_PORT=59999   # nothing listens here; curl exits 7

run_case() {
  local name="$1" trapdef="$2" script
  script=$(mktemp)
  cat > "$script" <<EOF
set -e
set -E
$trapdef
echo "  code=\$(curl -m2 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$DEAD_PORT/health 2>/dev/null)"
echo "REACHED_END"
EOF
  local out rc
  out=$(timeout 8 bash "$script" 2>&1); rc=$?
  rm -f "$script"
  # a trap that exec'd sleep infinity leaves orphans; reap them
  pkill -P 1 -f "sleep infinity" >/dev/null 2>&1 || true
  printf '%s|%s' "$rc" "$out"
}

OLD_TRAP='trap '"'"'rc=$?; echo "[go.sh] boot step failed rc=$rc -- keeping container ALIVE (degraded) for debug"; exec sleep infinity'"'"' ERR'

NEW_TRAP='_go_err_trap() {
  local rc=$?
  if [[ ${BASH_SUBSHELL:-0} -ne 0 || ${BASHPID:-$$} -ne $$ ]]; then
    return $rc
  fi
  echo "[go.sh] boot step failed rc=$rc -- keeping container ALIVE (degraded) for debug"
  exec sleep infinity
}
trap '"'"'_go_err_trap'"'"' ERR'

echo "=== CASE 1: OLD inline trap (expect: HANG -> timeout rc=124) ==="
r=$(run_case old "$OLD_TRAP"); old_rc=${r%%|*}; old_out=${r#*|}
echo "  rc=$old_rc"
echo "  out=${old_out:-<empty>}"

echo
echo "=== CASE 2: NEW subshell-safe trap (expect: COMPLETES, prints REACHED_END) ==="
r=$(run_case new "$NEW_TRAP"); new_rc=${r%%|*}; new_out=${r#*|}
echo "  rc=$new_rc"
echo "  out=${new_out:-<empty>}"

echo
FAIL=0
if [[ "$old_rc" == "124" ]]; then
  echo "  [OK]  old trap deadlocks as expected -- the test reproduces the bug"
else
  echo "  [!!]  old trap did NOT deadlock (rc=$old_rc) -- test is not exercising the bug"
  FAIL=1
fi
if [[ "$new_rc" == "0" && "$new_out" == *REACHED_END* ]]; then
  echo "  [OK]  new trap completes cleanly -- deadlock is fixed"
else
  echo "  [!!]  new trap did not complete cleanly (rc=$new_rc)"
  FAIL=1
fi

echo
[[ $FAIL -eq 0 ]] && echo "RESULT: PASS" || echo "RESULT: FAIL"
exit $FAIL
