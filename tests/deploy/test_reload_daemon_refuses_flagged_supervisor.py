"""FU-530 (graphify-kl-daily-refresh): tools/reload_daemon.sh must REFUSE to
relaunch a daemon whose authoritative supervisor form carries trailing flags.

reload_daemon.sh can only relaunch a daemon as
`daemon_wrapper.sh <name> <script>` with NO trailing args. graph_refresh and
loop_watch are launched by go.sh / watchdog.sh as a DIRECT self-looping loop --
`python3 .../graph_refresh.py --interval 900` -- precisely because
daemon_wrapper does not forward trailing flags. A bare relaunch would drop
`--interval`, and graph_refresh.py with no --interval is a ONE-SHOT that exits
0, which daemon_wrapper reads as "clean shutdown, do not respawn": the
15-minute refresher becomes a single run then permanent silence, logged as
healthy (measured 2026-09-24: two refreshes in 48h).

Two poles, driven through the shipped `--check-supervisor` predicate so the
test cannot drift from the code it guards:
  FLAGGED : a supervisor declaring `graph_refresh.py --interval 900` -> rc 2
  CLEAR   : a supervisor declaring a plain `daemon_wrapper.sh foo foo.py` -> rc 0
"""
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPT = os.path.join(REPO, "tools", "reload_daemon.sh")


def _check(name, supervisor_text, tmp_path):
    sup = tmp_path / "supervisor.sh"
    sup.write_text(supervisor_text)
    env = dict(os.environ, RELOAD_SUPERVISOR_FILES=str(sup))
    return subprocess.run(
        ["bash", SCRIPT, "--check-supervisor", name],
        capture_output=True, text=True, env=env,
    )


def test_flagged_interval_daemon_is_refused(tmp_path):
    # RED pole: the exact graph_refresh form from go.sh/watchdog.sh.
    p = _check(
        "graph_refresh",
        'nohup bash -c "while true; do python3 '
        "/home/workspace/zo_sentinel/tools/graph_refresh.py --interval 900; "
        'sleep 30; done" &\n',
        tmp_path,
    )
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert "FLAGGED" in p.stdout, p.stdout


def test_loop_watch_interval_daemon_is_refused(tmp_path):
    p = _check(
        "loop_watch",
        'nohup bash -c "while true; do python3 '
        "/home/workspace/zo_sentinel/loop_watch.py --interval 1800; "
        'sleep 30; done" &\n',
        tmp_path,
    )
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert "FLAGGED" in p.stdout, p.stdout


def test_plain_daemon_is_cleared(tmp_path):
    # GREEN pole: a daemon_wrapper-managed daemon with no trailing flag after
    # <name>.py must NOT be refused -- byte-identical reload path as before.
    p = _check(
        "gate_scheduler",
        "nohup bash $MESH/daemon_wrapper.sh gate_scheduler "
        "$SENTINEL/gate_scheduler.py >> $LOGS/gate_scheduler.log 2>&1 &\n",
        tmp_path,
    )
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    assert "CLEAR" in p.stdout, p.stdout
