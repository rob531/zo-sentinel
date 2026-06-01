"""Hermetic tests for the net-new code-artifact ingestor (zo_sentinel.ingestor).

No write_service, no host: artifacts are temp files, mesh_memory is the
InMemoryMeshStore. Proves the gate_8 contracts are reproduced (incl. the static
safety stop), the promote/quarantine + reverse-feed loop works, and the runner
stays DORMANT (writes nothing) until activated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from zo_sentinel.ingestor.contracts import (  # noqa: E402
    classify,
    static_safety_scan,
    validate_file,
)
from zo_sentinel.ingestor.model import ArtifactType, BuildArtifact, IngestAction  # noqa: E402
from zo_sentinel.ingestor.store import InMemoryMeshStore  # noqa: E402
from zo_sentinel.ingestor.ingestor import ArtifactIngestor  # noqa: E402


# --- sample artifact bodies -------------------------------------------------

GOOD_ENRICHMENT = '''
def compute_score(signal: dict):
    return 42.0, {"reason": "ok"}
'''

BAD_ENRICHMENT_RETURN = '''
def compute_score(signal: dict):
    return "not a number", {}
'''

DANGEROUS_PY = '''
import os  # noqa
DROP_IT = "DROP TABLE mesh_memory"
def run():
    pass
# module-level mutation a hallucination might emit:
SQL = "DROP TABLE mesh_memory;"
'''

GOOD_PLAIN_PY = '''
VALUE = 1
def helper():
    return VALUE
'''

ADMIN_HTML_OK = '<html><body><form><input name="x"><button>Go</button></form></body></html>'
ADMIN_HTML_SPA = '<html><body><div><input id="q"><button onclick="fetch(1)">Go</button></div></body></html>'
PLAIN_HTML_OK = '<html><body><h1>Dashboard</h1><table><tr><td>data</td></tr></table></body></html>'
EMPTY_HTML = '<!-- just a comment -->'


def _write(home: Path, name: str, body: str) -> str:
    (home / name).write_text(body, encoding="utf-8")
    return name


def _artifact(name: str, built_at: str = "2026-05-29T00:00:00+00:00") -> tuple[str, dict]:
    return (f"row_{name}", {"file": name, "built_at": built_at, "phase": "build"})


# --- contracts: classification ---------------------------------------------

class TestClassify:
    def test_enrichment(self):
        assert classify("community_signal_enrichment.py") == ArtifactType.ENRICHMENT_PY

    def test_admin_html(self):
        assert classify("admin_policies.html") == ArtifactType.ADMIN_HTML

    def test_plain_html(self):
        assert classify("dashboard.html") == ArtifactType.HTML

    def test_python(self):
        assert classify("risk_ranker.py") == ArtifactType.PYTHON

    def test_markdown(self):
        assert classify("NOTES.md") == ArtifactType.MARKDOWN


# --- contracts: static safety scan (the security stop) ----------------------

class TestSafetyScan:
    def test_drop_on_protected_table_flagged(self):
        assert static_safety_scan('x = "DROP TABLE mesh_memory"') is not None

    def test_delete_from_protected_flagged(self):
        assert static_safety_scan("DELETE FROM service_health WHERE 1=1") is not None

    def test_truncate_protected_flagged(self):
        assert static_safety_scan("TRUNCATE TABLE mcp_attestations") is not None

    def test_drop_on_unprotected_table_ok(self):
        # a scratch table is not a protected core table
        assert static_safety_scan("DROP TABLE tmp_scratch_123") is None

    def test_clean_source_ok(self):
        assert static_safety_scan(GOOD_PLAIN_PY) is None


# --- contracts: validate_file dispatch --------------------------------------

class TestValidateFile:
    def test_good_enrichment(self, tmp_path: Path):
        p = tmp_path / "x_enrichment.py"; p.write_text(GOOD_ENRICHMENT, encoding="utf-8")
        ok, contract, detail, safety = validate_file(p, ArtifactType.ENRICHMENT_PY)
        assert ok and contract == "enrichment_compute_score", detail

    def test_bad_enrichment_return_shape(self, tmp_path: Path):
        p = tmp_path / "y_enrichment.py"; p.write_text(BAD_ENRICHMENT_RETURN, encoding="utf-8")
        ok, contract, detail, safety = validate_file(p, ArtifactType.ENRICHMENT_PY)
        assert not ok and not safety

    def test_dangerous_py_blocked_before_import(self, tmp_path: Path):
        p = tmp_path / "danger.py"; p.write_text(DANGEROUS_PY, encoding="utf-8")
        ok, contract, detail, safety = validate_file(p, ArtifactType.PYTHON)
        assert not ok and safety and contract == "static_safety_scan"

    def test_good_plain_py_imports(self, tmp_path: Path):
        p = tmp_path / "ok.py"; p.write_text(GOOD_PLAIN_PY, encoding="utf-8")
        ok, contract, detail, safety = validate_file(p, ArtifactType.PYTHON)
        assert ok and contract == "python_import"

    def test_admin_html_form(self, tmp_path: Path):
        p = tmp_path / "admin_x.html"; p.write_text(ADMIN_HTML_OK, encoding="utf-8")
        assert validate_file(p, ArtifactType.ADMIN_HTML)[0]

    def test_admin_html_spa(self, tmp_path: Path):
        p = tmp_path / "admin_y.html"; p.write_text(ADMIN_HTML_SPA, encoding="utf-8")
        assert validate_file(p, ArtifactType.ADMIN_HTML)[0]

    def test_plain_html_ok(self, tmp_path: Path):
        p = tmp_path / "d.html"; p.write_text(PLAIN_HTML_OK, encoding="utf-8")
        assert validate_file(p, ArtifactType.HTML)[0]

    def test_empty_html_fails(self, tmp_path: Path):
        p = tmp_path / "e.html"; p.write_text(EMPTY_HTML, encoding="utf-8")
        assert not validate_file(p, ArtifactType.HTML)[0]


# --- model ------------------------------------------------------------------

class TestModel:
    def test_from_json_str(self):
        a = BuildArtifact.from_mesh_content(json.dumps({"file": "f.py", "built_at": "t"}))
        assert a and a.file == "f.py" and a.dedup_key == "f.py|t"

    def test_from_dict(self):
        a = BuildArtifact.from_mesh_content({"file": "g.py"})
        assert a and a.file == "g.py"

    def test_garbage_returns_none(self):
        assert BuildArtifact.from_mesh_content("not json") is None
        assert BuildArtifact.from_mesh_content({"no_file": 1}) is None


# --- ingestor: evaluate (no store interaction) ------------------------------

class TestEvaluate:
    def test_good_enrichment_promotes(self, tmp_path: Path):
        _write(tmp_path, "a_enrichment.py", GOOD_ENRICHMENT)
        ing = ArtifactIngestor(InMemoryMeshStore(), sentinel_home=str(tmp_path))
        v = ing.evaluate(BuildArtifact(file="a_enrichment.py"))
        assert v.ok and v.action is IngestAction.PROMOTE and v.fix_directive is None

    def test_dangerous_quarantines_with_safety_and_fix(self, tmp_path: Path):
        _write(tmp_path, "danger.py", DANGEROUS_PY)
        ing = ArtifactIngestor(InMemoryMeshStore(), sentinel_home=str(tmp_path))
        v = ing.evaluate(BuildArtifact(file="danger.py"))
        assert not v.ok and v.safety_block
        assert v.action is IngestAction.QUARANTINE
        assert v.fix_directive and v.fix_directive["source"] == "artifact_ingestor"

    def test_missing_output_file_quarantines(self, tmp_path: Path):
        # No _write: the declared output file was never materialised on disk
        # (investigate/diagnostic directive, or a build that emitted a row but
        # no file). Must QUARANTINE -- mirrors gate_8's built_file_missing so the
        # two graders agree instead of the ingestor false-promoting a phantom.
        ing = ArtifactIngestor(InMemoryMeshStore(), sentinel_home=str(tmp_path))
        v = ing.evaluate(BuildArtifact(file="never_built_enrichment.py"))
        assert not v.ok
        assert v.contract == "built_file_missing"
        assert v.action is IngestAction.QUARANTINE
        assert not v.safety_block
        assert v.fix_directive and v.fix_directive["source"] == "artifact_ingestor"


# --- ingestor: DORMANCY is the headline contract ----------------------------

class TestDormant:
    def test_dormant_writes_nothing(self, tmp_path: Path):
        _write(tmp_path, "ok.py", GOOD_PLAIN_PY)
        _write(tmp_path, "bad_enrichment.py", BAD_ENRICHMENT_RETURN)
        store = InMemoryMeshStore([_artifact("ok.py"), _artifact("bad_enrichment.py")])
        ing = ArtifactIngestor(store, sentinel_home=str(tmp_path))  # dormant
        assert ing.is_enabled() is False
        verdicts = ing.run_once()
        assert len(verdicts) == 2
        assert all(v.action_taken is False for v in verdicts)
        assert store.writes == []          # <-- nothing written while dormant

    def test_env_does_not_leak_enable(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("ARTIFACT_INGESTOR_ENABLED", raising=False)
        assert ArtifactIngestor(InMemoryMeshStore(), sentinel_home=str(tmp_path)).is_enabled() is False


# --- ingestor: ACTIVATED -- promote / quarantine / reverse-feed -------------

class TestActivated:
    def test_promote_writes_verdict_and_promotion(self, tmp_path: Path):
        _write(tmp_path, "ok.py", GOOD_PLAIN_PY)
        store = InMemoryMeshStore([_artifact("ok.py")])
        ing = ArtifactIngestor(store, sentinel_home=str(tmp_path), enabled=True)
        verdicts = ing.run_once()
        assert verdicts[0].ok and verdicts[0].action_taken
        assert len(store.writes_of_type("artifact_verdict")) == 1
        assert len(store.writes_of_type("artifact_promoted")) == 1
        assert len(store.writes_of_type("artifact_quarantined")) == 0

    def test_quarantine_reverse_feeds_fix_directive(self, tmp_path: Path):
        _write(tmp_path, "danger.py", DANGEROUS_PY)
        store = InMemoryMeshStore([_artifact("danger.py")])
        ing = ArtifactIngestor(store, sentinel_home=str(tmp_path), enabled=True)
        ing.run_once()
        assert len(store.writes_of_type("artifact_quarantined")) == 1
        directives = store.writes_of_type("build_directive")
        assert len(directives) == 1
        d = directives[0]
        assert d["agent_id"] == "zo_sentinel.directive"   # what goose_runner polls
        payload = json.loads(d["content"])
        assert payload["file"] == "danger.py"
        assert payload["origin"] == "artifact_ingestion_quarantine"

    def test_watermark_advances(self, tmp_path: Path):
        _write(tmp_path, "ok.py", GOOD_PLAIN_PY)
        store = InMemoryMeshStore([_artifact("ok.py", built_at="2026-05-29T12:00:00+00:00")])
        ing = ArtifactIngestor(store, sentinel_home=str(tmp_path), enabled=True)
        ing.run_once()
        assert store.get_watermark() == "2026-05-29T12:00:00+00:00"

    def test_heartbeat_written(self, tmp_path: Path):
        _write(tmp_path, "ok.py", GOOD_PLAIN_PY)
        store = InMemoryMeshStore([_artifact("ok.py")])
        ArtifactIngestor(store, sentinel_home=str(tmp_path), enabled=True).run_once()
        hbs = [r for (t, r) in store.writes if t == "service_health"]
        assert hbs and hbs[0]["service"] == "artifact_ingestor"

    def test_dedup_within_batch(self, tmp_path: Path):
        _write(tmp_path, "ok.py", GOOD_PLAIN_PY)
        # same file+built_at twice -> processed once
        dup = _artifact("ok.py")
        store = InMemoryMeshStore([dup, ("row_dup2", dup[1])])
        ing = ArtifactIngestor(store, sentinel_home=str(tmp_path), enabled=True)
        verdicts = ing.run_once()
        assert len(verdicts) == 1

    def test_activation_via_sentinel_file(self, tmp_path: Path):
        (tmp_path / ".ingestor_enabled").write_text("", encoding="utf-8")
        ing = ArtifactIngestor(InMemoryMeshStore(), sentinel_home=str(tmp_path))
        assert ing.is_enabled() is True


# --- HttpMeshStore degrades gracefully when write_service is down -----------

class TestHttpStoreDegrades:
    def test_post_swallows_network_error(self, monkeypatch):
        import requests
        from zo_sentinel.ingestor.store import HttpMeshStore

        def boom(*a, **k):
            raise requests.ConnectionError("write_service down")

        monkeypatch.setattr(requests, "post", boom)
        s = HttpMeshStore("http://127.0.0.1:8772")
        # none of these may raise -- a down service must not crash a caller
        assert s.get_watermark() is None
        assert s.read_build_artifacts(None, 10) == []
        assert s.write("mesh_memory", {"a": 1}) is False
        assert s.last_error and "ConnectionError" in s.last_error

    def test_reachable_false_on_error(self, monkeypatch):
        import requests
        from zo_sentinel.ingestor.store import HttpMeshStore

        def boom(*a, **k):
            raise requests.ConnectionError("down")

        monkeypatch.setattr(requests, "get", boom)
        assert HttpMeshStore("http://127.0.0.1:8772").reachable() is False


# --- watermark filtering on read --------------------------------------------

def test_read_respects_watermark(tmp_path: Path):
    store = InMemoryMeshStore([
        _artifact("old.py", built_at="2026-05-01T00:00:00+00:00"),
        _artifact("new.py", built_at="2026-05-29T00:00:00+00:00"),
    ])
    store.set_watermark("2026-05-15T00:00:00+00:00")
    rows = store.read_build_artifacts(store.get_watermark(), 40)
    files = [c["file"] for (_id, c) in rows]
    assert files == ["new.py"]
