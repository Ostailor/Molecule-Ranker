from __future__ import annotations

import json
from pathlib import Path

from molecule_ranker.codex_backbone.guardrails import redact_secrets
from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask, CodexTaskResult


class CodexAuditLogger:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.audit_path = root_dir / ".molecule-ranker" / "codex_backbone_audit.jsonl"

    def write(
        self,
        task: CodexTask,
        result: CodexTaskResult,
        *,
        prompt_text: str,
        command: list[str],
        config: CodexBackboneConfig,
    ) -> None:
        if not config.codex_store_transcripts:
            return
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "task": task.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
            "command": command,
            "prompt_text": prompt_text,
        }
        if config.codex_redact_secrets:
            record["prompt_text"] = redact_secrets(str(record["prompt_text"]))
        with self.audit_path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
