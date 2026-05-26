from __future__ import annotations

import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from molecule_ranker.review.dossier import (
    DossierWriterAgent,
    render_dossier_markdown,
)
from molecule_ranker.review.schemas import CandidateDossier, ReviewWorkspace, ValidationHandoff
from molecule_ranker.review.validation_handoff import build_validation_handoff

_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "env",
    "password",
    "secret",
    "token",
)
_OMITTED_TEXT_KEYS = {
    "abstract",
    "article_text",
    "body",
    "full_text",
    "methods",
    "paper_text",
    "text",
}
_RESTRICTED_DETAIL_KEYS = {
    "lab_protocol",
    "reaction_conditions",
    "reagents",
    "synthesis_route",
}
_PROCEDURAL_TERMS = (
    "concentration",
    "dose",
    "dosage",
    "incubat",
    "mg/kg",
    "protocol",
    "reagent",
    "reaction condition",
    "synthesis instructions",
    "synthesis route",
    "temperature",
)


class ReviewExportResult(BaseModel):
    workspace_id: str
    output_path: Path
    output_format: str
    files: list[str]


def write_json(path: Path, payload: BaseModel | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, BaseModel):
        data = payload.model_dump(mode="json")
    else:
        data = payload
    path.write_text(json.dumps(_sanitize_payload(data), indent=2, sort_keys=True) + "\n")


def write_dossier_markdown(path: Path, dossier: CandidateDossier) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_dossier_markdown(dossier))


def export_review_package(
    workspace: ReviewWorkspace,
    output_path: str | Path,
    *,
    output_format: str = "json",
) -> ReviewExportResult:
    fmt = output_format.lower()
    path = Path(output_path)
    if fmt == "zip":
        return _export_zip_package(workspace, path)
    if fmt not in {"json", "markdown"}:
        raise ValueError("output_format must be json, markdown, or zip")
    _write_package_directory(workspace, path, output_format=fmt)
    return ReviewExportResult(
        workspace_id=workspace.workspace_id,
        output_path=path,
        output_format=fmt,
        files=_relative_files(path),
    )


def render_workspace_markdown(workspace: ReviewWorkspace) -> str:
    summary = _workspace_summary(workspace)
    lines = [
        f"# Review Workspace: {workspace.disease_name}",
        "",
        "Human decisions are expert triage labels, not clinical conclusions.",
        "Model-generated scores do not establish safety, efficacy, binding, or synthesizability.",
        "",
        f"- Workspace ID: `{workspace.workspace_id}`",
        f"- Run ID: `{workspace.run_id}`",
        f"- Created: `{workspace.created_at.isoformat()}`",
        f"- Review items: {len(workspace.review_items)}",
        f"- Priority distribution: {_format_distribution(summary['priority_distribution'])}",
        f"- Status distribution: {_format_distribution(summary['status_distribution'])}",
        "",
        "## Review Items",
        "",
    ]
    for item in workspace.review_items:
        lines.extend(
            [
                f"### {item.candidate_name}",
                "",
                f"- Review item ID: `{item.review_item_id}`",
                f"- Origin: {item.candidate_origin}",
                f"- Priority: {item.priority_bucket}",
                f"- Status: {item.review_status}",
                f"- Score: {item.score}",
                f"- Confidence: {item.confidence}",
                f"- Targets: {', '.join(item.target_symbols) or 'n/a'}",
                "",
            ]
        )
        if item.candidate_origin == "generated":
            lines.extend(["Generated hypothesis; no direct experimental evidence.", ""])
        if item.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in item.warnings)
            lines.append("")
    if workspace.decisions:
        lines.extend(["## Reviewer Decisions", ""])
        for decision in workspace.decisions:
            lines.extend(
                [
                    f"- `{decision.created_at.isoformat()}` "
                    f"{decision.reviewer.reviewer_id}: {decision.decision} "
                    f"on `{decision.review_item_id}`",
                    f"  Rationale: {decision.rationale}",
                ]
            )
        lines.append("")
    if workspace.comments:
        lines.extend(["## Reviewer Comments", ""])
        for comment in workspace.comments:
            lines.append(
                f"- `{comment.created_at.isoformat()}` {comment.reviewer.reviewer_id} "
                f"({comment.comment_type}) on `{comment.review_item_id}`: "
                f"{comment.comment_text}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _export_zip_package(workspace: ReviewWorkspace, output_path: Path) -> ReviewExportResult:
    zip_path = output_path if output_path.suffix == ".zip" else output_path.with_suffix(".zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        package_dir = Path(temp_dir) / "review_export"
        _write_package_directory(workspace, package_dir, output_format="json")
        files = _relative_files(package_dir)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for relative in files:
                archive.write(package_dir / relative, arcname=relative)
    return ReviewExportResult(
        workspace_id=workspace.workspace_id,
        output_path=zip_path,
        output_format="zip",
        files=files,
    )


def _write_package_directory(
    workspace: ReviewWorkspace,
    output_dir: Path,
    *,
    output_format: str,
) -> None:
    if output_dir.exists():
        if output_dir.is_file():
            raise ValueError(f"Package output path is a file: {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "workspace.json", workspace)
    write_json(output_dir / "review_queue.json", {"review_items": workspace.review_items})
    write_json(output_dir / "decisions.json", {"decisions": workspace.decisions})
    write_json(output_dir / "comments.json", {"comments": workspace.comments})
    write_json(output_dir / "audit_log.json", {"audit_events": workspace.audit_events})
    write_json(output_dir / "source_artifact_manifest.json", _source_artifact_manifest(workspace))
    (output_dir / "limitations.md").write_text(_limitations_markdown())
    (output_dir / "README.md").write_text(_readme_markdown(workspace))
    if output_format == "markdown":
        (output_dir / "workspace.md").write_text(render_workspace_markdown(workspace))
    _write_candidate_packets(workspace, output_dir, output_format=output_format)
    _write_export_manifest(workspace, output_dir)


def _write_candidate_packets(
    workspace: ReviewWorkspace,
    output_dir: Path,
    *,
    output_format: str,
) -> None:
    dossier_agent = DossierWriterAgent()
    for item in workspace.review_items:
        dossier = dossier_agent.build_dossier(workspace, item.review_item_id)
        handoff = build_validation_handoff(
            workspace,
            item.review_item_id,
            evidence_packet_paths={
                "dossier": f"dossiers/{item.review_item_id}."
                f"{'md' if output_format == 'markdown' else 'json'}"
            },
        )
        if output_format == "markdown":
            _write_text(
                output_dir / "dossiers" / f"{item.review_item_id}.md",
                render_dossier_markdown(dossier),
            )
            _write_text(
                output_dir / "validation_handoffs" / f"{item.review_item_id}.md",
                _render_handoff_markdown(handoff),
            )
        else:
            write_json(output_dir / "dossiers" / f"{item.review_item_id}.json", dossier)
            write_json(output_dir / "validation_handoffs" / f"{item.review_item_id}.json", handoff)


def _write_export_manifest(workspace: ReviewWorkspace, output_dir: Path) -> None:
    files = sorted(
        path.relative_to(output_dir).as_posix()
        for path in output_dir.rglob("*")
        if path.is_file() and path.name != "export_manifest.json"
    )
    files.append("export_manifest.json")
    write_json(
        output_dir / "export_manifest.json",
        {
            "workspace_id": workspace.workspace_id,
            "package_type": "review_export",
            "files": files,
            "limitations": [
                "Research-use expert review package only.",
                "Reviewer decisions are expert triage labels, not clinical conclusions.",
                "Article text, local caches, credentials, and environment variables are omitted.",
            ],
        },
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_sanitize_document_text(text))


def _render_handoff_markdown(handoff: ValidationHandoff) -> str:
    lines = [
        f"# Validation Handoff: {handoff.candidate_name}",
        "",
        handoff.disclaimer,
        "",
        f"- Review item ID: `{handoff.review_item_id}`",
        f"- Origin: {handoff.candidate_origin}",
        f"- Disease: {handoff.disease_name}",
        f"- Targets: {', '.join(handoff.target_symbols) or 'n/a'}",
        "",
        "## Validation Questions",
        "",
        *[f"- {question}" for question in handoff.validation_questions],
        "",
        "## Suggested High-Level Categories",
        "",
        *[f"- {assay_class}" for assay_class in handoff.suggested_assay_classes],
        "",
        "## Required Expert Reviews",
        "",
        *[f"- {role}" for role in handoff.required_expert_reviews],
        "",
        "## Key Risks To Check",
        "",
        *[f"- {risk}" for risk in handoff.key_risks_to_check],
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _source_artifact_manifest(workspace: ReviewWorkspace) -> dict[str, Any]:
    artifacts: dict[str, str] = {}
    for source in [workspace.metadata, *(item.metadata for item in workspace.review_items)]:
        raw_paths = source.get("artifact_paths") if isinstance(source, dict) else None
        if not isinstance(raw_paths, dict):
            continue
        for key, value in raw_paths.items():
            key_text = str(key)
            path_text = str(value)
            if _safe_artifact_reference(key_text, path_text):
                artifacts[key_text] = path_text
    return {
        "workspace_id": workspace.workspace_id,
        "artifacts": dict(sorted(artifacts.items())),
        "omitted": [
            "cache files",
            "credentials and environment variables",
            "full article text",
            "restricted procedural details",
        ],
    }


def _safe_artifact_reference(key: str, path: str) -> bool:
    combined = f"{key} {path}".lower()
    if any(part in combined for part in _SECRET_KEY_PARTS):
        return False
    blocked_path_parts = (".cache", "__pycache__", "/cache/", "\\cache\\", ".env")
    return not any(part in combined for part in blocked_path_parts)


def _workspace_summary(workspace: ReviewWorkspace) -> dict[str, dict[str, int]]:
    priority_distribution: dict[str, int] = {}
    status_distribution: dict[str, int] = {}
    for item in workspace.review_items:
        priority_distribution[item.priority_bucket] = (
            priority_distribution.get(item.priority_bucket, 0) + 1
        )
        status_distribution[item.review_status] = status_distribution.get(item.review_status, 0) + 1
    return {
        "priority_distribution": dict(sorted(priority_distribution.items())),
        "status_distribution": dict(sorted(status_distribution.items())),
    }


def _format_distribution(payload: dict[str, int]) -> str:
    if not payload:
        return "none recorded"
    return ", ".join(f"{key}: {value}" for key, value in sorted(payload.items()))


def _limitations_markdown() -> str:
    return (
        "# Limitations\n\n"
        "- This package is for research triage and expert review only.\n"
        "- It is not medical advice and does not provide patient treatment instructions.\n"
        "- Reviewer decisions are expert triage labels, not clinical conclusions.\n"
        "- Computational scores do not prove safety, efficacy, binding, or buildability.\n"
        "- Generated hypotheses have no direct experimental evidence unless separately "
        "documented.\n"
        "- Citation metadata may be included; full article text is omitted.\n"
    )


def _readme_markdown(workspace: ReviewWorkspace) -> str:
    return (
        f"# Review Export Package: {workspace.disease_name}\n\n"
        "This package contains locally generated expert-review workflow artifacts for "
        "research use. It preserves model-generated evidence summaries and human review "
        "decisions as separate records.\n\n"
        "Do not use this package for clinical decisions, patient care, dosing, or "
        "operational laboratory execution. It omits local caches, credentials, environment "
        "variables, and full copyrighted article text.\n"
    )


def _relative_files(root: Path) -> list[str]:
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())


def _sanitize_payload(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _sanitize_payload(value.model_dump(mode="json"))
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if (
                _is_sensitive_key(key_lower)
                or key_lower in _OMITTED_TEXT_KEYS
                or key_lower in _RESTRICTED_DETAIL_KEYS
            ):
                continue
            sanitized[key_text] = _sanitize_payload(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return _sanitize_text(value)
    return value


def _is_sensitive_key(key_lower: str) -> bool:
    return any(part in key_lower for part in _SECRET_KEY_PARTS)


def _sanitize_text(value: str) -> str:
    lower = value.lower()
    if any(part in lower for part in _SECRET_KEY_PARTS):
        return "[omitted: credential or environment detail]"
    if any(term in lower for term in _PROCEDURAL_TERMS):
        return "[omitted: restricted procedural detail]"
    return value


def _sanitize_document_text(value: str) -> str:
    lines = [_sanitize_text(line) for line in value.splitlines()]
    trailing = "\n" if value.endswith("\n") else ""
    return "\n".join(lines) + trailing
