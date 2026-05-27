from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class WorkspaceAuditLogger:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        self.audit_path = self.root_dir / ".molecule-ranker" / "workspace_audit.jsonl"

    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "event_type": event_type,
            "created_at": datetime.now(UTC).isoformat(),
            "payload": payload,
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
