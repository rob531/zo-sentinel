"""Tests for zo_sentinel.anchor_refill -- the self-refilling anchor.

Real-variance fixtures: a spec with built + unbuilt candidates, KL design docs
with bullets and prose, quarantined/retired names that must be excluded, and
the idempotence + gating properties that make the loop non-fragile.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from zo_sentinel import anchor_refill  # noqa: E402


def _mk_sentinel(tmp_path, missing_candidates=1):
    """Sentinel home: PRODUCT_SPEC with one built + N unbuilt candidates,
    docs/ with a design doc naming three new modules, directives layout."""
    root = tmp_path / "sentinel"
    (root / "docs").mkdir(parents=True)
    (root / "directives" / "retired" / "20260702T000000Z").mkdir(parents=True)
    quarantine = tmp_path / "state" / "quarantine"
    quarantine.mkdir(parents=True)

    unbuilt = "\n".join(
        f"- directive candidate: `unbuilt_mod_{i}.py` -- an unbuilt thing."
        for i in range(missing_candidates))
    (root / "PRODUCT_SPEC.md").write_text(
        "## Appendix X (directive candidates, NOT YET BUILT)\n\n"
        "- directive candidate: `built_mod.py` -- already built.\n"
        f"{unbuilt}\n", encoding="utf-8")
    (root / "built_mod.py").write_text("# built\n", encoding="utf-8")

    (root / "docs" / "DESIGN_NEXT_PHASE.md").write_text(
        "# Design: next phase\n\n"
        "The follow-on work needs a `trend_alert_service.py` that watches\n"
        "tier changes over time and emits alert rows via write_service.\n\n"
        "- `coverage_report_api.py` -- FastAPI GET /coverage: scored vs total\n"
        "  registry counts per source, read via the app DB session.\n\n"
        "A quarantined idea `poison_module.py` and the retired\n"
        "`retired_module.py` must not come back. Tests live in\n"
        "`test_trend_alert_service.py` (never a target).\n\n"
        "Docs like `some_doc.md` are not build targets either.\n",
        encoding="utf-8")
    (quarantine / "build_poison_module.failed.json").write_text("{}",
                                                                encoding="utf-8")
    (root / "directives" / "retired" / "20260702T000000Z" /
     "gen_1_build_retired_module.json").write_text("{}", encoding="utf-8")
    return root, quarantine


def test_refills_when_anchor_low_with_provenance(tmp_path):
    root, q = _mk_sentinel(tmp_path, missing_candidates=1)  # 1 < threshold 5
    stats = anchor_refill.run_refill(root, quarantine_dir=q)

    assert stats["reason"] == "refilled"
    assert set(stats["files"]) == {"trend_alert_service.py",
                                   "coverage_report_api.py"}
    auto = (root / anchor_refill.AUTO_ANCHOR_NAME).read_text(encoding="utf-8")
    assert "- directive candidate: `trend_alert_service.py`" in auto
    assert "[auto-anchor: DESIGN_NEXT_PHASE.md#L" in auto
    # description came from the KL paragraph, not synthesized
    assert "watches tier changes" in auto.replace("\n", " ")


def test_mined_candidates_visible_to_the_gaps_extractor(tmp_path):
    root, q = _mk_sentinel(tmp_path, missing_candidates=1)
    anchor_refill.run_refill(root, quarantine_dir=q)
    combined = ((root / "PRODUCT_SPEC.md").read_text(encoding="utf-8") + "\n" +
                (root / anchor_refill.AUTO_ANCHOR_NAME).read_text(encoding="utf-8"))
    cands = anchor_refill.spec_candidate_files(combined)
    assert "trend_alert_service.py" in cands
    assert "coverage_report_api.py" in cands


def test_idempotent_second_run_appends_nothing(tmp_path):
    root, q = _mk_sentinel(tmp_path, missing_candidates=1)
    first = anchor_refill.run_refill(root, quarantine_dir=q)
    assert first["appended"] == 2
    size_after_first = (root / anchor_refill.AUTO_ANCHOR_NAME).stat().st_size

    second = anchor_refill.run_refill(root, quarantine_dir=q)
    # The two mined candidates now COUNT toward missing (1+2=3 < 5), so a
    # second pass may run -- but must find nothing new to append.
    assert second["appended"] == 0
    assert second["reason"] in ("nothing_new_in_kl", "anchor_sufficient")
    assert (root / anchor_refill.AUTO_ANCHOR_NAME).stat().st_size == size_after_first


def test_full_anchor_is_never_touched(tmp_path):
    root, q = _mk_sentinel(tmp_path, missing_candidates=7)  # 7 >= threshold 5
    stats = anchor_refill.run_refill(root, quarantine_dir=q)
    assert stats["reason"] == "anchor_sufficient"
    assert not (root / anchor_refill.AUTO_ANCHOR_NAME).exists()


def test_quarantined_retired_tests_and_docs_excluded(tmp_path):
    root, q = _mk_sentinel(tmp_path, missing_candidates=1)
    stats = anchor_refill.run_refill(root, quarantine_dir=q)
    assert "poison_module.py" not in stats["files"]        # durable quarantine
    assert "retired_module.py" not in stats["files"]       # janitor-retired
    assert not any(f.startswith("test_") for f in stats["files"])
    assert not any(f.endswith(".md") for f in stats["files"])


def test_bounded_by_max_new(tmp_path):
    root, q = _mk_sentinel(tmp_path, missing_candidates=1)
    stats = anchor_refill.run_refill(root, max_new=1, quarantine_dir=q)
    assert stats["appended"] == 1


def test_missing_spec_fails_open(tmp_path):
    stats = anchor_refill.run_refill(tmp_path / "nowhere")
    assert stats["reason"] == "spec_unreadable"
    assert stats["appended"] == 0


def test_gate_default_off_sentinel_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv(anchor_refill.ENV_FLAG, raising=False)
    d = tmp_path / "directives"
    d.mkdir()
    assert anchor_refill.enabled(d) is False
    (d / anchor_refill.SENTINEL_NAME).write_text("1", encoding="utf-8")
    assert anchor_refill.enabled(d) is True
    (d / anchor_refill.SENTINEL_NAME).write_text("0", encoding="utf-8")
    assert anchor_refill.enabled(d) is False
    monkeypatch.setenv(anchor_refill.ENV_FLAG, "1")
    assert anchor_refill.enabled(d) is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
