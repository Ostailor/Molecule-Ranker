from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field
from sqlalchemy import insert, select

from molecule_ranker.codex_backbone.guardrails import (
    check_output,
    collect_allowed_refs_from_artifacts,
    detect_fake_external_ids,
    detect_protocol_or_synthesis_text,
    redact_secrets,
)
from molecule_ranker.codex_backbone.schemas import CodexTask, CodexTaskResult
from molecule_ranker.integrations.credentials import redact_secret_values
from molecule_ranker.integrations.schemas import (
    SECRET_CONFIG_KEYS,
    EntityMapping,
    ExternalRecordRef,
    SyncJob,
    SyncRecord,
)
from molecule_ranker.platform.database import PlatformDatabase, artifact_records
from molecule_ranker.utils import slugify

CodexIntegrationTaskType = Literal[
    "suggest_schema_mapping",
    "explain_sync_failure",
    "summarize_external_record",
    "suggest_mapping_review_questions",
    "draft_export_summary",
    "compare_internal_external_record",
]


class CodexIntegrationProvider(Protocol):
    def run_task(self, task: CodexTask) -> CodexTaskResult: ...


class CodexIntegrationArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: f"codex-int-{uuid.uuid4().hex[:16]}")
    task_type: CodexIntegrationTaskType
    status: str
    output_json: dict[str, Any] | None = None
    output_text: str = ""
    artifact_refs: list[str] = Field(default_factory=list)
    external_record_refs: list[str] = Field(default_factory=list)
    sync_record_ids: list[str] = Field(default_factory=list)
    guardrail_warnings: list[str] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodexIntegrationAssistant:
    """Codex-backed integration assistance that never mutates integrations directly."""

    def __init__(
        self,
        provider: CodexIntegrationProvider,
        *,
        working_directory: str | Path = ".",
        database: PlatformDatabase | None = None,
        org_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        self.provider = provider
        self.working_directory = Path(working_directory).resolve()
        self.database = database
        self.org_id = org_id
        self.project_id = project_id

    def suggest_schema_mapping(
        self,
        *,
        external_records: list[dict[str, Any]],
        data_contract: dict[str, Any] | None = None,
    ) -> CodexIntegrationArtifact:
        return self._run(
            "suggest_schema_mapping",
            {
                "external_records": external_records,
                "data_contract": data_contract or {},
            },
            "Suggest schema mappings from supplied external record fields only.",
        )

    def explain_sync_failure(
        self,
        *,
        sync_job: SyncJob,
        sync_records: list[SyncRecord],
    ) -> CodexIntegrationArtifact:
        return self._run(
            "explain_sync_failure",
            {
                "sync_job": sync_job.model_dump(mode="json"),
                "sync_records": [record.model_dump(mode="json") for record in sync_records],
            },
            "Explain the sync failure using only supplied sync job and sync records.",
        )

    def summarize_external_record(
        self,
        *,
        external_ref: ExternalRecordRef,
        payload: dict[str, Any],
    ) -> CodexIntegrationArtifact:
        return self._run(
            "summarize_external_record",
            {
                "external_ref": external_ref.model_dump(mode="json"),
                "payload": payload,
            },
            "Summarize this external record without inventing missing fields.",
        )

    def suggest_mapping_review_questions(
        self,
        *,
        mapping: EntityMapping,
        context: dict[str, Any] | None = None,
    ) -> CodexIntegrationArtifact:
        return self._run(
            "suggest_mapping_review_questions",
            {
                "mapping": mapping.model_dump(mode="json"),
                "context": context or {},
            },
            "Draft human mapping-review questions. Do not approve or reject the mapping.",
        )

    def draft_export_summary(
        self,
        *,
        export_preview: dict[str, Any],
    ) -> CodexIntegrationArtifact:
        return self._run(
            "draft_export_summary",
            {"export_preview": export_preview},
            "Draft a summary for this export preview. Do not write to external systems.",
        )

    def compare_internal_external_record(
        self,
        *,
        internal_record: dict[str, Any],
        external_record: dict[str, Any],
    ) -> CodexIntegrationArtifact:
        return self._run(
            "compare_internal_external_record",
            {
                "internal_record": internal_record,
                "external_record": external_record,
            },
            "Compare the supplied internal and external records without creating evidence.",
        )

    def _run(
        self,
        task_type: CodexIntegrationTaskType,
        context: dict[str, Any],
        prompt: str,
    ) -> CodexIntegrationArtifact:
        context_path = self._write_context(task_type, context)
        task = CodexTask(
            task_id=slugify(f"integration-{task_type}-{uuid.uuid4().hex[:8]}"),
            task_type=task_type,  # type: ignore[arg-type]
            prompt=_integration_prompt(prompt),
            working_directory=str(self.working_directory),
            input_artifact_paths=[str(context_path)],
            allowed_commands=[],
            forbidden_commands=[
                "curl",
                "wget",
                "ssh",
                "scp",
                "printenv",
                "cat .env",
                "git push",
                "rm -rf",
            ],
            expected_output_format="json",
            timeout_seconds=300,
            require_json=True,
            metadata={
                "integration_assistance_only": True,
                "cannot_activate_mappings": True,
                "cannot_write_external_systems": True,
                "cannot_create_evidence": True,
                "cannot_create_assay_results": True,
                "cannot_enqueue_sync": True,
            },
        )
        result = self.provider.run_task(task)
        return self._package(task_type, task, result, context_path, context)

    def _package(
        self,
        task_type: CodexIntegrationTaskType,
        task: CodexTask,
        result: CodexTaskResult,
        context_path: Path,
        context: dict[str, Any],
    ) -> CodexIntegrationArtifact:
        raw_output_json = result.output_json
        raw_output_text = "\n".join(
            [
                json.dumps(raw_output_json or {}, sort_keys=True),
                result.output_text,
                result.stdout,
            ]
        )
        unsafe_warnings = detect_prohibited_integration_actions(
            raw_output_text
        )
        output_json = _postprocess_output_json(task_type, result.output_json, context)
        output_text = redact_secret_values(redact_secrets(result.output_text or result.stdout))
        context_refs = _external_record_refs(context)
        context_artifact_refs = _context_artifact_refs(context)
        sync_record_ids = _sync_record_ids(context)
        artifact_refs, allowed_citations = collect_allowed_refs_from_artifacts(
            [str(context_path)]
        )
        artifact_refs.update(context_refs)
        artifact_refs.update(context_artifact_refs)
        artifact_refs.update(sync_record_ids)
        guarded = check_output(
            result.model_copy(update={"output_json": output_json, "output_text": output_text}),
            artifact_refs,
            allowed_citations,
        )
        warnings = _dedupe([*guarded.guardrail_warnings, *unsafe_warnings])
        for warning in detect_fake_external_ids(
            json.dumps(output_json or {}) + output_text,
            artifact_refs,
        ):
            if warning not in warnings:
                warnings.append(warning)
        if detect_protocol_or_synthesis_text(json.dumps(output_json or {}) + output_text):
            output_json = _strip_unsafe_payload(output_json)
            output_text = ""
        artifact = CodexIntegrationArtifact(
            task_type=task_type,
            status="guardrail_failed" if warnings else guarded.status,
            output_json=output_json,
            output_text=output_text,
            artifact_refs=_dedupe(
                _artifact_refs(output_json, [str(context_path), *context_artifact_refs])
            ),
            external_record_refs=_dedupe(_external_record_refs(output_json) + context_refs),
            sync_record_ids=sync_record_ids,
            guardrail_warnings=warnings,
            metadata={
                "codex_task_id": task.task_id,
                "codex_task_type": task.task_type,
                "codex_result_status": result.status,
                "artifacts_read": result.artifacts_read,
                "sync_record_ids": sync_record_ids,
                "usage_summary": result.usage_summary,
                "codex_backbone_artifact": True,
                "connector_execution": "not_run",
            },
        )
        stored_id = self._store_artifact(artifact)
        if stored_id:
            artifact = artifact.model_copy(
                update={
                    "metadata": {
                        **artifact.metadata,
                        "stored_artifact_id": stored_id,
                    }
                }
            )
        return artifact

    def _write_context(self, task_type: str, context: dict[str, Any]) -> Path:
        context_dir = self.working_directory / ".molecule-ranker" / "integration-codex-context"
        context_dir.mkdir(parents=True, exist_ok=True)
        context_id = f"integration-codex-context:{task_type}:{uuid.uuid4().hex[:8]}"
        path = context_dir / f"{slugify(task_type)}-{context_id.rsplit(':', 1)[-1]}.json"
        payload = {
            "artifact_id": context_id,
            "task_type": task_type,
            "context": _redact_json(context),
            "boundaries": [
                "Codex cannot activate mappings.",
                "Codex cannot write to external systems.",
                "Codex cannot enqueue sync jobs.",
                "Codex cannot invent external IDs, registry IDs, Benchling IDs, assay runs, "
                "or assay results.",
                "Codex cannot create EvidenceItem records.",
                "No lab protocols, synthesis instructions, dosing, or treatment guidance.",
            ],
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return path

    def _store_artifact(self, artifact: CodexIntegrationArtifact) -> str | None:
        if self.database is None:
            return None
        payload = json.dumps(artifact.model_dump(mode="json"), indent=2, sort_keys=True).encode()
        digest = hashlib.sha256(payload).hexdigest()
        artifact_id = f"artifact-{digest[:16]}"
        output_dir = self.database.root_dir / ".molecule-ranker" / "codex_backbone_outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{artifact_id}.json"
        path.write_bytes(payload)
        with self.database.engine.begin() as connection:
            existing = connection.execute(
                select(artifact_records.c.artifact_id).where(
                    artifact_records.c.artifact_id == artifact_id
                )
            ).first()
            if existing is None:
                connection.execute(
                    insert(artifact_records).values(
                        artifact_id=artifact_id,
                        org_id=self.org_id,
                        project_id=self.project_id,
                        run_id=None,
                        artifact_type="codex_backbone",
                        path=str(path),
                        sha256=digest,
                        size_bytes=len(payload),
                        provenance_json={
                            "task_type": artifact.task_type,
                            "integration_assistance_only": True,
                        },
                        created_at=datetime.now(UTC),
                        metadata_json={"codex_integration_artifact_id": artifact.artifact_id},
                    )
                )
        return artifact_id


def _integration_prompt(prompt: str) -> str:
    return (
        f"{prompt} Use only the supplied integration context artifact. Return JSON only. "
        "Cite artifact_refs and external_record_refs. Do not activate mappings, enqueue sync, "
        "write to external systems, invent external IDs, invent registry or Benchling IDs, "
        "invent assay runs/results, create EvidenceItem, or provide protocols, synthesis, dosing, "
        "or treatment guidance."
    )


def _postprocess_output_json(
    task_type: str,
    output_json: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, Any] | None:
    if output_json is None:
        return None
    updated = _redact_json(dict(output_json))
    refs = _dedupe(list(updated.get("external_record_refs") or []) + _external_record_refs(context))
    updated["external_record_refs"] = refs
    artifact_refs = list(updated.get("artifact_refs") or [])
    if not artifact_refs:
        updated["artifact_refs"] = _context_artifact_refs(context) or ["integration-codex-context"]
    if task_type == "explain_sync_failure":
        updated["sync_record_ids"] = _dedupe(
            list(updated.get("sync_record_ids") or []) + _sync_record_ids(context)
        )
    if task_type == "suggest_schema_mapping":
        suggestions = []
        for suggestion in list(updated.get("suggested_mappings") or []):
            if isinstance(suggestion, dict):
                suggestions.append(
                    {
                        **suggestion,
                        "status": "pending_review",
                        "mapping_method": "codex_suggested_pending_validation",
                    }
                )
        updated["suggested_mappings"] = suggestions
    for forbidden in ["activate_mapping", "enqueue_sync", "external_write", "evidence_item"]:
        updated.pop(forbidden, None)
    return updated


def detect_prohibited_integration_actions(text: str) -> list[str]:
    lowered = text.lower()
    checks = [
        ("enqueue_sync", "Codex output attempted to enqueue or run an integration sync."),
        ("run_sync", "Codex output attempted to enqueue or run an integration sync."),
        (
            "molecule-ranker integration sync",
            "Codex output attempted to enqueue or run an integration sync.",
        ),
        ("activate_mapping", "Codex output attempted to activate an integration mapping."),
        ("status\": \"active", "Codex output attempted to activate an integration mapping."),
        ("external_write", "Codex output attempted to write to an external system."),
        ("write_enabled", "Codex output attempted to request external write-enabled mode."),
        ("evidenceitem", "Codex output attempted to create an EvidenceItem."),
        ("evidence_item", "Codex output attempted to create an EvidenceItem."),
        ("assay_result\":", "Codex output attempted to create or invent an assay result."),
        ("assay_results\":", "Codex output attempted to create or invent assay results."),
    ]
    warnings: list[str] = []
    for marker, warning in checks:
        if marker in lowered and warning not in warnings:
            warnings.append(warning)
    return warnings


def _strip_unsafe_payload(output_json: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "status": "guardrail_failed",
        "summary": "Unsafe Codex integration output was suppressed.",
        "artifact_refs": list((output_json or {}).get("artifact_refs") or []),
        "external_record_refs": list((output_json or {}).get("external_record_refs") or []),
    }


def _external_record_refs(value: Any) -> list[str]:
    refs: list[str] = []
    _collect_external_refs(value, refs)
    return _dedupe(refs)


def _collect_external_refs(value: Any, refs: list[str]) -> None:
    if isinstance(value, ExternalRecordRef):
        refs.append(_external_ref_string(value.model_dump(mode="json")))
    elif isinstance(value, dict):
        if {"external_system_id", "external_record_id"} <= set(value):
            refs.append(_external_ref_string(value))
        raw_ref = value.get("external_ref")
        if isinstance(raw_ref, dict):
            refs.append(_external_ref_string(raw_ref))
        for item in value.values():
            _collect_external_refs(item, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_external_refs(item, refs)


def _external_ref_string(value: dict[str, Any]) -> str:
    return ":".join(
        [
            str(value.get("external_system_id") or ""),
            str(value.get("external_record_type") or "record"),
            str(value.get("external_record_id") or ""),
        ]
    )


def _artifact_refs(output_json: dict[str, Any] | None, defaults: list[str]) -> list[str]:
    refs = list((output_json or {}).get("artifact_refs") or [])
    return refs or defaults


def _context_artifact_refs(value: Any) -> list[str]:
    refs: list[str] = []
    _collect_context_artifact_refs(value, refs)
    return _dedupe(refs)


def _collect_context_artifact_refs(value: Any, refs: list[str]) -> None:
    if isinstance(value, dict):
        for key, raw in value.items():
            lowered = str(key).lower()
            if lowered == "artifact_id" and raw not in (None, "", []):
                refs.append(str(raw))
            elif lowered == "artifact_ids" and isinstance(raw, list):
                refs.extend(str(item) for item in raw if item not in (None, "", []))
            _collect_context_artifact_refs(raw, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_context_artifact_refs(item, refs)


def _sync_record_ids(value: Any) -> list[str]:
    refs: list[str] = []
    _collect_sync_record_ids(value, refs)
    return _dedupe(refs)


def _collect_sync_record_ids(value: Any, refs: list[str]) -> None:
    if isinstance(value, dict):
        for key, raw in value.items():
            if str(key).lower() == "sync_record_id" and raw not in (None, "", []):
                refs.append(str(raw))
            _collect_sync_record_ids(raw, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_sync_record_ids(item, refs)


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in SECRET_CONFIG_KEYS or any(
                marker in normalized for marker in SECRET_CONFIG_KEYS
            ):
                redacted[str(key)] = "[REDACTED]"
            else:
                redacted[str(key)] = _redact_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return redact_secret_values(redact_secrets(value))
    return value


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value and value not in deduped:
            deduped.append(value)
    return deduped
