from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from molecule_ranker.e2e.lineage import ExternalLineageTracker
from molecule_ranker.e2e.schemas import (
    EndToEndResultBundle,
    EndToEndValidationResult,
    EndToEndWorkflow,
    WorkflowLineageRecord,
)
from molecule_ranker.v3 import v3_product_contract_payload

REQUIRED_LIMITATIONS = [
    "This bundle is a research and operations summary, not scientific evidence.",
    "No medical advice is provided.",
    (
        "No wet-lab instructions, synthesis details, dose guidance, "
        "or treatment guidance are provided."
    ),
    "No claims of activity, safety, or efficacy are made.",
    "Codex summaries are drafted only from deterministic bundle data.",
    "Antibody generation is disabled by default and requires approved tool plugins.",
    (
        "Generated antibodies are computational hypotheses only and do not establish "
        "binding, neutralization, treatment, safety, developability, or manufacturability."
    ),
]

FORBIDDEN_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\blab\s+protocols?\b", re.I), "wet-lab instructions"),
    (re.compile(r"\bsynthesis\s+instructions?\b", re.I), "synthesis details"),
    (re.compile(r"\bdosing\b", re.I), "dose guidance"),
    (re.compile(r"\bclaims?\s+of\s+activity\b", re.I), "unsupported claims"),
    (re.compile(r"\bactivity\b", re.I), "screening observation"),
    (re.compile(r"\bsafety\b", re.I), "risk review"),
    (re.compile(r"\befficacy\b", re.I), "outcome review"),
    (re.compile(r"\bneutraliz(?:e|es|ation)\b", re.I), "functional claim"),
    (re.compile(r"\btreats?\b", re.I), "clinical-use claim"),
    (re.compile(r"\bcures?\b", re.I), "clinical-use claim"),
    (re.compile(r"\bmanufacturable\b", re.I), "manufacturing risk review"),
    (re.compile(r"\bvalidated\s+binder\b", re.I), "review candidate"),
)


class ResultBundleModel(BaseModel):
    @field_validator("*")
    @classmethod
    def require_timezone_aware_datetimes(cls, value: Any) -> Any:
        if isinstance(value, datetime) and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("timestamps must be timezone-aware")
        return value


class ResultBundleInput(ResultBundleModel):
    workflow: EndToEndWorkflow
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    candidate_summary: dict[str, Any] = Field(default_factory=dict)
    generated_molecule_summary: dict[str, Any] = Field(default_factory=dict)
    biologics_summary: dict[str, Any] = Field(default_factory=dict)
    evidence_summary: dict[str, Any] = Field(default_factory=dict)
    developability_summary: dict[str, Any] = Field(default_factory=dict)
    experimental_evidence_summary: dict[str, Any] = Field(default_factory=dict)
    graph_hypothesis_summary: dict[str, Any] = Field(default_factory=dict)
    portfolio_campaign_summary: dict[str, Any] = Field(default_factory=dict)
    integration_lineage_summary: dict[str, Any] = Field(default_factory=dict)
    evaluation_summary: dict[str, Any] = Field(default_factory=dict)
    codex_summary: dict[str, Any] = Field(default_factory=dict)
    approval_summary: dict[str, Any] = Field(default_factory=dict)
    guardrail_summary: dict[str, Any] = Field(default_factory=dict)
    lineage_records: list[WorkflowLineageRecord] = Field(default_factory=list)
    validation_result: EndToEndValidationResult | None = None
    next_recommended_actions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GeneratedResultBundle(ResultBundleModel):
    bundle: EndToEndResultBundle
    files: dict[str, Path]


class EndToEndResultBundleGenerator:
    """Generate safe, deterministic end-to-end result bundle artifacts."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def generate(
        self,
        bundle_input: ResultBundleInput,
        *,
        output_dir: str | Path,
    ) -> GeneratedResultBundle:
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)

        sanitized = self._sanitize_input(bundle_input)
        bundle = self._build_bundle(sanitized)
        bundle = ExternalLineageTracker(
            workflow_id=bundle.workflow_id,
            records=sanitized.lineage_records,
            now=self._now,
        ).include_in_bundle(bundle)

        files = {
            "json": target / "e2e_result_bundle.json",
            "markdown": target / "e2e_result_bundle.md",
            "lineage": target / "e2e_lineage.json",
            "validation": target / "e2e_validation.json",
        }
        files["json"].write_text(
            json.dumps(bundle.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        files["markdown"].write_text(self._markdown(bundle), encoding="utf-8")
        files["lineage"].write_text(
            json.dumps(
                {
                    "workflow_id": bundle.workflow_id,
                    "lineage_records": [
                        record.model_dump(mode="json")
                        for record in sanitized.lineage_records
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        files["validation"].write_text(
            json.dumps(self._validation_payload(sanitized), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return GeneratedResultBundle(bundle=bundle, files=files)

    def _build_bundle(self, bundle_input: ResultBundleInput) -> EndToEndResultBundle:
        workflow = bundle_input.workflow
        artifact_manifest = self._artifact_manifest(bundle_input.artifacts)
        integration_summary = {
            **bundle_input.integration_lineage_summary,
            "lineage_record_count": len(bundle_input.lineage_records),
            "external_record_refs": sum(
                len(record.external_record_refs)
                for record in bundle_input.lineage_records
            ),
        }
        metadata = {
            **bundle_input.metadata,
            "workflow_summary": self._workflow_summary(workflow),
            "artifact_manifest": artifact_manifest,
            "developability_summary": bundle_input.developability_summary,
            "experimental_evidence_summary": bundle_input.experimental_evidence_summary,
            "graph_hypothesis_summary": bundle_input.graph_hypothesis_summary,
            "biologics_summary": bundle_input.biologics_summary,
            "codex_subagent_copilot_summary": bundle_input.codex_summary,
            "approval_summary": bundle_input.approval_summary,
            "guardrail_summary": bundle_input.guardrail_summary,
            "next_recommended_actions": bundle_input.next_recommended_actions,
            "bundle_is_scientific_evidence": False,
            "v3_product_contract": v3_product_contract_payload(),
        }
        return EndToEndResultBundle(
            bundle_id=f"e2e-bundle-{workflow.workflow_id}",
            workflow_id=workflow.workflow_id,
            project_id=workflow.project_id,
            disease_name=workflow.disease_name,
            result_summary=(
                f"Workflow {workflow.workflow_id} completed with status {workflow.status}. "
                "This is a deterministic research and operations summary."
            ),
            key_artifact_ids=[
                str(item["artifact_id"])
                for item in artifact_manifest
                if item.get("artifact_id")
            ],
            candidate_summary=bundle_input.candidate_summary,
            generated_summary=bundle_input.generated_molecule_summary,
            biologics_summary=bundle_input.biologics_summary,
            evidence_summary={
                **bundle_input.evidence_summary,
                "experimental_evidence": bundle_input.experimental_evidence_summary,
            },
            review_summary={
                "developability": bundle_input.developability_summary,
                "guardrails": bundle_input.guardrail_summary,
            },
            campaign_summary=bundle_input.portfolio_campaign_summary,
            evaluation_summary=bundle_input.evaluation_summary,
            integration_summary=integration_summary,
            limitations=self._limitations(bundle_input.limitations),
            created_at=self._now(),
            metadata=metadata,
        )

    def _sanitize_input(self, bundle_input: ResultBundleInput) -> ResultBundleInput:
        payload = bundle_input.model_dump(mode="python")
        sanitized = self._sanitize_json(payload)
        return ResultBundleInput.model_validate(sanitized)

    def _sanitize_json(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {key: self._sanitize_json(raw) for key, raw in value.items()}
        if isinstance(value, list):
            return [self._sanitize_json(item) for item in value]
        if isinstance(value, str):
            return self._sanitize_text(value)
        return value

    def _sanitize_text(self, text: str) -> str:
        sanitized = text
        for pattern, replacement in FORBIDDEN_TEXT_PATTERNS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized

    def _limitations(self, limitations: list[str]) -> list[str]:
        seen: set[str] = set()
        merged = [*limitations, *REQUIRED_LIMITATIONS]
        result: list[str] = []
        for limitation in merged:
            sanitized = self._sanitize_text(limitation)
            key = sanitized.lower()
            if key not in seen:
                seen.add(key)
                result.append(sanitized)
        return result

    def _artifact_manifest(self, artifacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        manifest = []
        for artifact in artifacts:
            manifest.append(
                {
                    "artifact_id": artifact.get("artifact_id"),
                    "artifact_type": artifact.get("artifact_type"),
                    "path": artifact.get("path"),
                    "source": artifact.get("source", "deterministic_workflow"),
                }
            )
        return manifest

    def _workflow_summary(self, workflow: EndToEndWorkflow) -> dict[str, Any]:
        return {
            "workflow_id": workflow.workflow_id,
            "name": workflow.name,
            "workflow_type": workflow.workflow_type,
            "status": workflow.status,
            "mode": workflow.mode,
            "project_id": workflow.project_id,
            "campaign_id": workflow.campaign_id,
            "requested_by": workflow.requested_by,
            "started_at": workflow.started_at,
            "completed_at": workflow.completed_at,
        }

    def _validation_payload(self, bundle_input: ResultBundleInput) -> dict[str, Any]:
        if bundle_input.validation_result is None:
            return {
                "workflow_id": bundle_input.workflow.workflow_id,
                "validation_present": False,
                "guardrails_passed": False,
                "warnings": ["No validation result supplied."],
            }
        payload = bundle_input.validation_result.model_dump(mode="json")
        payload["validation_present"] = True
        return payload

    def _markdown(self, bundle: EndToEndResultBundle) -> str:
        metadata = bundle.metadata
        lines = [
            f"# End-to-End Result Bundle: {bundle.workflow_id}",
            "",
            "## Workflow Summary",
            "",
            f"- Status: `{bundle.metadata['workflow_summary']['status']}`",
            f"- Mode: `{bundle.metadata['workflow_summary']['mode']}`",
            f"- Project: `{bundle.project_id}`",
            "",
            "## Artifact Manifest",
            "",
        ]
        artifact_manifest = metadata.get("artifact_manifest") or []
        if artifact_manifest:
            for artifact in artifact_manifest:
                lines.append(
                    f"- `{artifact.get('artifact_id')}` "
                    f"({artifact.get('artifact_type')})"
                )
        else:
            lines.append("- No artifacts registered.")
        summary_lines = [
            ("Candidates", bundle.candidate_summary),
            ("Generated molecules", bundle.generated_summary),
            ("Biologics and antibodies", bundle.biologics_summary),
            ("Evidence", bundle.evidence_summary),
            ("Developability", metadata.get("developability_summary", {})),
            ("Graph and hypotheses", metadata.get("graph_hypothesis_summary", {})),
            ("Portfolio and campaign", bundle.campaign_summary),
            ("Integration lineage", bundle.integration_summary),
            ("Evaluation", bundle.evaluation_summary),
            (
                "Codex/subagent/co-pilot",
                metadata.get("codex_subagent_copilot_summary", {}),
            ),
            ("Approvals", metadata.get("approval_summary", {})),
            ("Guardrails", metadata.get("guardrail_summary", {})),
        ]
        lines.extend(
            [
                "",
                "## Summaries",
                "",
                *[
                    f"- {label}: `{json.dumps(payload, sort_keys=True)}`"
                    for label, payload in summary_lines
                ],
                "",
                "## Limitations",
                "",
            ]
        )
        lines.extend(f"- {limitation}" for limitation in bundle.limitations)
        lines.extend(["", "## Next Recommended Actions", ""])
        actions = metadata.get("next_recommended_actions") or []
        if actions:
            lines.extend(f"- {action}" for action in actions)
        else:
            lines.append("- No next actions recorded.")
        return self._sanitize_text("\n".join(lines).rstrip() + "\n")


__all__ = [
    "EndToEndResultBundle",
    "EndToEndResultBundleGenerator",
    "GeneratedResultBundle",
    "ResultBundleInput",
]
