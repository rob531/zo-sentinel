"""Regression for #12770: a RELATIVE launch token in go.sh was resolved against
the checker's cwd, not the `cd` that actually applies at launch.

go.sh runs the WorldAgent daemon as::

    cd /home/workspace/world_agent
    nohup python run.py --daemon >> $LOGS/world_agent.log 2>&1 &

so the real install path is ``/home/workspace/world_agent/run.py``. The
declaration parser captured the bare ``run.py`` token and later ``.resolve()``'d
it against wherever the checker happened to run from -- non-deterministically
``/home/workspace/run.py`` (daemon, cwd /home/workspace) or
``/home/workspace/zo_sentinel/run.py`` (one-shot) -- and NEVER the world_agent
path, so the live daemon read MISSING forever (chairman issue #12770). This is
the declaration-side mirror of the #12768 process-side fix.

NEGATIVE CONTROLS ARE THE POINT. The anchor must not invent a directory: a bare
relative launch with no preceding `cd` stays relative (we do not guess), and an
absolute/`$MESH` token is untouched. Otherwise the cure becomes a way to make
any launch resolve to any path -- a rubber stamp.

    POSITIVE   `cd /abs; nohup python run.py`  -> declared /abs/run.py
    NEGATIVE 1 bare `nohup python run.py`       -> stays relative run.py
    NEGATIVE 2 `nohup python3 $MESH/x.py`       -> $MESH-anchored, cd ignored
    NEGATIVE 3 nearest preceding cd wins (a later cd must NOT leak back)
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SRC = HERE / "tools" / "registration_drift_check.py"
spec = importlib.util.spec_from_file_location("registration_drift_check", SRC)
rdc = importlib.util.module_from_spec(spec)
sys.modules["registration_drift_check"] = rdc
spec.loader.exec_module(rdc)


def test_relative_token_anchored_on_preceding_cd():
    go = (
        "pkill -f 'run.py --daemon' 2>/dev/null || true\n"
        "cd /home/workspace/world_agent\n"
        "nohup python run.py --daemon >> $LOGS/world_agent.log 2>&1 &\n"
        "cd /home/workspace\n"
    )
    out = rdc.declared_from_go(go)
    assert out["run"]["script"] == "/home/workspace/world_agent/run.py"


def test_bare_relative_without_cd_stays_relative():
    # No preceding `cd` -> we must NOT invent an anchor.
    go = "nohup python run.py --daemon >> /tmp/x.log 2>&1 &\n"
    out = rdc.declared_from_go(go)
    assert out["run"]["script"] == "run.py"


def test_absolute_mesh_token_untouched_by_cd():
    go = (
        "cd /home/workspace/world_agent\n"
        "nohup python3 $MESH/run_manager.py daemon >> $LOGS/manager.log 2>&1 &\n"
    )
    out = rdc.declared_from_go(go)
    # $MESH-anchored; the cd above must not redirect it into world_agent.
    assert out["run_manager"]["script"] == str(rdc.MESH / "run_manager.py")


def test_nearest_preceding_cd_wins():
    go = (
        "cd /home/workspace/other\n"
        "echo nothing\n"
        "cd /home/workspace/world_agent\n"
        "nohup python run.py --daemon >> /tmp/x.log 2>&1 &\n"
    )
    out = rdc.declared_from_go(go)
    assert out["run"]["script"] == "/home/workspace/world_agent/run.py"
