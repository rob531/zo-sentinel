"""CI guard for zo-sentinel #12366: the Dockerfile COPYs root modules explicitly,
so a new root module imported by the mounted API surface but not added to a COPY
line is silently absent from the image (ModuleNotFoundError at mount/startup -- this
kept the unauth_access signal dark through #12256 until #12312). This test fails the
build if any such module is missing, forcing an explicit `COPY <name>.py /srv/`."""
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECK = ROOT / "tools" / "check_image_module_completeness.py"


def test_mount_required_root_modules_are_copied_into_image():
    r = subprocess.run([sys.executable, str(CHECK)], capture_output=True, text=True)
    assert r.returncode == 0, (
        "A root module required by the mounted API surface is not COPY'd into the "
        "Docker image. Add a `COPY <name>.py /srv/` line to the Dockerfile.\n" + r.stdout + r.stderr
    )
