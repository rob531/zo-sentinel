"""output_file sanity gate — rejects the doubled-leading-prefix poison
(e.g. admin_admin_ui_suite.py) that ghost-loops the builder forever. Covers the
canonical helper and the promoter's defence-in-depth rejection."""
import json
import pytest
from zo_sentinel.build_completion import output_file_is_sane
from zo_sentinel.promoters import proposed_to_pending_promoter as promoter


def test_doubled_leading_prefix_rejected():
    ok, reason = output_file_is_sane("admin_admin_ui_suite.py")
    assert ok is False and "doubled" in reason.lower()


@pytest.mark.parametrize("name", [
    "admin_ui_suite.py",
    "admin_exemptions.html",
    "breaker_actions/breaker_action_reset_snow_connector.py",
    "enrichments_writer_daemon_v3.py",
    "snow_inbound_webhook_handler.py",
    "signal_analyser_tool_schema_extensions.py",
])
def test_legitimate_names_pass(name):
    ok, _ = output_file_is_sane(name)
    assert ok is True, name


def test_empty_or_none_is_sane():
    assert output_file_is_sane("")[0] is True
    assert output_file_is_sane(None)[0] is True


def _directive(output_file):
    return {"task": "build_admin_ui_suite", "handler": "generate_file",
            "output_file": output_file, "description": "x" * 60}


def test_promoter_rejects_poisoned_and_promotes_corrected(tmp_path):
    proposed = tmp_path / "proposed"; pending = tmp_path / "pending"
    proposed.mkdir(); pending.mkdir()
    poison = proposed / "gen_cad3877a_build_admin_ui_suite.json"
    poison.write_text(json.dumps(_directive("admin_admin_ui_suite.py")))
    r1 = promoter.run_once(proposed, pending, min_age_secs=0, max_per_cycle=10,
                           directives_root=tmp_path)
    assert r1["rejected"] == 1
    assert not poison.exists()
    assert (proposed / (poison.name + ".rejected")).exists()

    good = proposed / "gen_deadbeef_build_admin_ui_suite.json"
    good.write_text(json.dumps(_directive("admin_ui_suite.py")))
    r2 = promoter.run_once(proposed, pending, min_age_secs=0, max_per_cycle=10,
                           directives_root=tmp_path)
    assert r2["promoted"] == 1
    assert (pending / good.name).exists()
