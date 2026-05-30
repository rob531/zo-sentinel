"""
model.py -- data model for the artifact ingestor.

A BuildArtifact mirrors the `build_artifact` mesh_memory row that the builder
writes (agent_id=t1.zo_sentinel_builder, memory_type=build_artifact):
    content = {file, built_at, phase, bytes, interface, task}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ArtifactType(str, Enum):
    ENRICHMENT_PY = "enrichment_py"   # *_enrichment.py -> compute_score contract
    ADMIN_HTML = "admin_html"         # admin_*.html    -> interactive contract
    HTML = "html"                     # *.html          -> parses + non-empty
    PYTHON = "python"                 # *.py            -> imports cleanly
    MARKDOWN = "markdown"             # *.md            -> non-empty
    UNKNOWN = "unknown"


class IngestAction(str, Enum):
    PROMOTE = "promote"        # passed -> approved for go-live
    QUARANTINE = "quarantine"  # failed -> held + fix-directive emitted
    SKIP = "skip"              # already processed / not actionable


@dataclass
class BuildArtifact:
    """One generated file the builder registered in mesh_memory."""
    file: str
    built_at: str = ""
    phase: str = ""
    bytes: int = 0
    interface: str = ""
    task: str = ""
    source_row_id: Optional[str] = None   # mesh_memory id, if known

    @property
    def dedup_key(self) -> str:
        return f"{self.file}|{self.built_at}"

    @classmethod
    def from_mesh_content(cls, content: Any, row_id: Optional[str] = None) -> Optional["BuildArtifact"]:
        """Parse a mesh_memory build_artifact `content` (JSON str or dict).
        Returns None if it isn't a recognisable artifact record."""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return None
        if not isinstance(content, dict) or "file" not in content:
            return None
        return cls(
            file=str(content.get("file", "")),
            built_at=str(content.get("built_at", "")),
            phase=str(content.get("phase", "")),
            bytes=int(content.get("bytes", 0) or 0),
            interface=str(content.get("interface", "")),
            task=str(content.get("task", "")),
            source_row_id=row_id,
        )


@dataclass
class IngestVerdict:
    """Outcome of validating one artifact."""
    artifact: BuildArtifact
    artifact_type: ArtifactType
    ok: bool
    contract: str                       # which contract was applied
    detail: str = ""
    action: IngestAction = IngestAction.SKIP
    action_taken: bool = False          # False while dormant (planned only)
    safety_block: bool = False          # True if the static safety scan failed
    fix_directive: Optional[dict] = field(default=None)

    def to_record(self) -> dict:
        return {
            "file": self.artifact.file,
            "built_at": self.artifact.built_at,
            "artifact_type": self.artifact_type.value,
            "ok": self.ok,
            "contract": self.contract,
            "detail": self.detail[:1000],
            "action": self.action.value,
            "action_taken": self.action_taken,
            "safety_block": self.safety_block,
        }
