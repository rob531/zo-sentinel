"""Tests for the file-based lessons index (closed-loop memory producer).
File-based by design (zero DB load on the build hot path); every fn best-effort."""
import json
from zo_sentinel.build_lessons import record_lesson, resolve_lessons, open_lessons_for


def test_record_creates_open_lesson(tmp_path):
    L = record_lesson(tmp_path, "admin_ui_suite.py", "build_admin_ui_suite",
                      "ghost_no_output", "declared output never produced", severity=3,
                      when="2026-06-19T00:00:00Z")
    assert L["status"] == "open" and L["recurrence"] == 1
    assert L["subject_ref"] == "admin_ui_suite.py"
    assert L["first_seen"] == "2026-06-19T00:00:00Z"
    opens = open_lessons_for(tmp_path, "admin_ui_suite.py")
    assert len(opens) == 1 and opens[0]["task_type"] == "ghost_no_output"


def test_repeat_bumps_recurrence_not_new_file(tmp_path):
    for i in range(5):
        record_lesson(tmp_path, "x.py", "build_x", "ghost_no_output", "again", when=f"t{i}")
    # exactly one file for the subject (no write amplification)
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1
    L = json.loads(files[0].read_text())
    assert L["recurrence"] == 5 and L["status"] == "open"
    assert L["first_seen"] == "t0" and L["last_seen"] == "t4"


def test_resolve_on_green_then_no_open(tmp_path):
    record_lesson(tmp_path, "y.py", "build_y", "non_compiling", "tier0 fail")
    assert resolve_lessons(tmp_path, "y.py") is True
    assert open_lessons_for(tmp_path, "y.py") == []          # resolved -> not surfaced
    assert resolve_lessons(tmp_path, "y.py") is False        # idempotent


def test_reopen_after_resolve(tmp_path):
    record_lesson(tmp_path, "z.py", "build_z", "ghost_no_output", "fail1")
    resolve_lessons(tmp_path, "z.py")
    L = record_lesson(tmp_path, "z.py", "build_z", "ghost_no_output", "fail2")
    assert L["status"] == "open" and L["recurrence"] == 2
    assert "resolved_at" not in L
    assert len(open_lessons_for(tmp_path, "z.py")) == 1


def test_doubled_path_subject_safe_filename(tmp_path):
    # subject can be a path; filename must be safe and stable
    record_lesson(tmp_path, "breaker_actions/foo.py", "build_foo", "x", "y")
    assert len(list(tmp_path.glob("*.json"))) == 1
    assert open_lessons_for(tmp_path, "breaker_actions/foo.py")


def test_missing_subject_safe(tmp_path):
    assert open_lessons_for(tmp_path, "never.py") == []
    assert resolve_lessons(tmp_path, "never.py") is False
