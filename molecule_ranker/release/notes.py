from __future__ import annotations

from pathlib import Path
from typing import Any


def render_release_notes(manifest: dict[str, Any]) -> str:
    version = manifest.get("version", "unknown")
    git_commit = manifest.get("git_commit", "unknown")
    build_timestamp = manifest.get("build_timestamp", "unknown")
    limitations = manifest.get("known_limitations", [])
    return "\n".join(
        [
            f"# molecule-ranker {version} Release Notes",
            "",
            "## Release Scope",
            "",
            "V1.0 ships molecule-ranker as a validated internal research platform MVP. "
            "This is a stabilization, validation, release-readiness, and operational-quality "
            "release, not a feature-expansion release.",
            "",
            "## Included",
            "",
            "- End-to-end golden workflows in deterministic mocked validation mode.",
            "- Versioned artifact and API contracts for V1.0 platform outputs.",
            "- Security, guardrail, provenance, integration, deployment, and release checks.",
            "- Operator, admin, and user documentation with backup/restore runbooks.",
            "- Synthetic demo project artifacts clearly labeled as non-evidence examples.",
            "",
            "## Safety And Integrity",
            "",
            "- research use only",
            "- no medical advice",
            "- no clinical claims",
            "- no lab protocols",
            "- no synthesis instructions",
            "- no dosing",
            "- generated molecules require validation",
            "- Codex outputs are assistant artifacts, not biomedical evidence",
            "",
            "## Contracts",
            "",
            f"- Artifact contract version: {manifest.get('artifact_contract_version', 'unknown')}",
            f"- API contract version: {manifest.get('api_contract_version', 'unknown')}",
            "",
            "## Build",
            "",
            f"- Git commit: {git_commit}",
            f"- Build timestamp: {build_timestamp}",
            f"- Dependency lock hash: {manifest.get('dependency_lock_hash', 'unknown')}",
            "",
            "## Known Limitations",
            "",
            *[f"- {item}" for item in limitations],
            "",
        ]
    )


def write_release_notes(notes: str, output_path: str | Path) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(notes)
    return target
