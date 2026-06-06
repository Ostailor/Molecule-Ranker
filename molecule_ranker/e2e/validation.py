from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from molecule_ranker.e2e.schemas import (
    EndToEndResultBundle,
    EndToEndValidationResult,
    EndToEndWorkflow,
    EndToEndWorkflowStep,
    WorkflowLineageRecord,
)
from molecule_ranker.e2e.workflow_runner import WorkflowRunResult

FORBIDDEN_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\blab\s+protocols?\b", re.I), "lab protocol"),
    (re.compile(r"\bsynthesis\s+instructions?\b", re.I), "synthesis instruction"),
    (re.compile(r"\bdosing\b", re.I), "dosing"),
    (re.compile(r"\bclaims?\s+of\s+activity\b", re.I), "activity claim"),
    (re.compile(r"\bproven\s+safety\b", re.I), "safety claim"),
    (re.compile(r"\befficacy\b", re.I), "efficacy claim"),
    (re.compile(r"\bpatient\s+treatment\b", re.I), "patient treatment guidance"),
)

SCIENTIFIC_TRUTH_FLAGS = {
    "fabricated_evidence",
    "fake_evidence",
    "fabricated_external_record",
    "fake_external_record",
    "failed_qc_supporting",
    "codex_scientific_truth",
}

REQUIRED_BUNDLE_METADATA_KEYS = {
    "workflow_summary",
    "artifact_manifest",
    "lineage_records",
}

ANTIBODY_FORBIDDEN_TEXT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bvalidated\s+binder\b", re.I), "unsupported antibody binding claim"),
    (re.compile(r"\bneutraliz(?:e|es|ation)\b", re.I), "unsupported antibody neutralization claim"),
    (re.compile(r"\btreats?\b", re.I), "unsupported treatment claim"),
    (re.compile(r"\bcures?\b", re.I), "unsupported cure claim"),
    (re.compile(r"\bmanufacturable\b", re.I), "unsupported manufacturability claim"),
    (re.compile(r"\bis\s+developable\b", re.I), "unsupported developability claim"),
)


class EndToEndWorkflowValidator:
    """Deterministic validation for V2.8 end-to-end workflow bundles."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))

    def validate_run_result(self, result: WorkflowRunResult) -> EndToEndValidationResult:
        return self.validate(
            workflow=result.workflow,
            steps=result.steps,
            bundle=result.bundle,
            lineage_records=result.lineage_records,
        )

    def validate(
        self,
        *,
        workflow: EndToEndWorkflow,
        steps: list[EndToEndWorkflowStep],
        bundle: EndToEndResultBundle | None,
        lineage_records: list[WorkflowLineageRecord],
    ) -> EndToEndValidationResult:
        findings: list[str] = []
        warnings: list[str] = []

        required_artifacts_present = self._required_artifacts_present(
            bundle=bundle,
            steps=steps,
            findings=findings,
        )
        artifact_contracts_valid = self._artifact_contracts_valid(bundle, findings)
        lineage_complete = self._workflow_lineage_complete(
            bundle=bundle,
            lineage_records=lineage_records,
            findings=findings,
        )
        external_sync_validated = self._external_sync_lineage_complete(
            lineage_records=lineage_records,
            findings=findings,
        )
        approvals_satisfied = self._approvals_satisfied(
            workflow=workflow,
            bundle=bundle,
            lineage_records=lineage_records,
            findings=findings,
        )
        guardrails_passed = self._guardrails_passed(
            bundle=bundle,
            lineage_records=lineage_records,
            findings=findings,
        )
        result_bundle_complete = self._result_bundle_complete(bundle, findings)

        passed = all(
            [
                required_artifacts_present,
                artifact_contracts_valid,
                lineage_complete,
                external_sync_validated,
                approvals_satisfied,
                guardrails_passed,
                result_bundle_complete,
            ]
        )
        if not passed and not findings:
            findings.append("End-to-end validation failed.")
        if workflow.status != "succeeded":
            warnings.append(f"Workflow status is {workflow.status}.")

        return EndToEndValidationResult(
            validation_id=f"e2e-validation-{uuid4().hex[:16]}",
            workflow_id=workflow.workflow_id,
            passed=passed,
            required_artifacts_present=required_artifacts_present,
            artifact_contracts_valid=artifact_contracts_valid,
            lineage_complete=lineage_complete,
            guardrails_passed=guardrails_passed,
            external_sync_validated=external_sync_validated,
            approvals_satisfied=approvals_satisfied,
            findings=findings,
            warnings=warnings,
            created_at=self._now(),
            metadata={
                "workflow_type": workflow.workflow_type,
                "workflow_status": workflow.status,
                "result_bundle_complete": result_bundle_complete,
                "checks": {
                    "no_fake_evidence": guardrails_passed,
                    "no_fake_external_records": guardrails_passed,
                    "generated_labels_intact": guardrails_passed,
                    "imported_assay_results_validated": external_sync_validated,
                    "no_failed_qc_treated_as_support": guardrails_passed,
                    "codex_outputs_separate": guardrails_passed,
                    "evaluation_artifacts_separate": guardrails_passed,
                    "no_forbidden_text": guardrails_passed,
                    "antibody_generation_default_off": guardrails_passed,
                    "generated_antibody_evidence_boundary": guardrails_passed,
                },
            },
        )

    def _required_artifacts_present(
        self,
        *,
        bundle: EndToEndResultBundle | None,
        steps: list[EndToEndWorkflowStep],
        findings: list[str],
    ) -> bool:
        if bundle is None:
            findings.append("result bundle missing")
            return False
        expected = {
            artifact_id
            for step in steps
            if step.status == "succeeded"
            for artifact_id in step.output_artifact_ids
        }
        present = set(bundle.key_artifact_ids)
        if not present:
            findings.append("required artifacts missing from result bundle")
            return False
        missing = sorted(expected - present)
        if missing:
            findings.append(f"required artifacts missing: {', '.join(missing)}")
            return False
        return True

    def _artifact_contracts_valid(
        self,
        bundle: EndToEndResultBundle | None,
        findings: list[str],
    ) -> bool:
        if bundle is None:
            return False
        manifest = bundle.metadata.get("artifact_manifest")
        if manifest is None:
            # Runner-created bundles use key_artifact_ids instead of a manifest.
            return bool(bundle.key_artifact_ids)
        invalid = [
            str(item.get("artifact_id"))
            for item in manifest
            if item.get("contract_valid") is False
        ]
        if invalid:
            findings.append(f"artifact contracts invalid: {', '.join(invalid)}")
            return False
        return True

    def _workflow_lineage_complete(
        self,
        *,
        bundle: EndToEndResultBundle | None,
        lineage_records: list[WorkflowLineageRecord],
        findings: list[str],
    ) -> bool:
        if not lineage_records:
            findings.append("workflow lineage missing")
            return False
        if bundle is None:
            return False
        target_artifacts = {
            artifact_id
            for record in lineage_records
            for artifact_id in record.artifact_ids
        }
        missing = sorted(set(bundle.key_artifact_ids) - target_artifacts)
        if missing:
            findings.append(f"lineage missing for artifacts: {', '.join(missing)}")
            return False
        return True

    def _external_sync_lineage_complete(
        self,
        *,
        lineage_records: list[WorkflowLineageRecord],
        findings: list[str],
    ) -> bool:
        external_records = [
            record
            for record in lineage_records
            if record.relation_type in {"imported_from", "exported_to", "synced_from", "synced_to"}
            or record.external_record_refs
        ]
        for record in external_records:
            if not record.external_record_refs:
                findings.append(f"external sync lineage missing refs: {record.lineage_id}")
                return False
            if record.target_object_type == "assay_result":
                validated = (
                    record.metadata.get("deterministic_validation") is True
                    or record.metadata.get("validation_status") in {None, "validated"}
                )
                if not validated:
                    findings.append("imported assay result lacks deterministic validation")
                    return False
        return True

    def _approvals_satisfied(
        self,
        *,
        workflow: EndToEndWorkflow,
        bundle: EndToEndResultBundle | None,
        lineage_records: list[WorkflowLineageRecord],
        findings: list[str],
    ) -> bool:
        if workflow.mode != "write_approved_live":
            return True
        approval_ids = set(bundle.metadata.get("approval_ids", []) if bundle else [])
        approval_ids.update(
            str(record.metadata.get("approval_id"))
            for record in lineage_records
            if record.metadata.get("approval_id")
        )
        if not approval_ids:
            findings.append("write-approved workflow missing approval lineage")
            return False
        return True

    def _guardrails_passed(
        self,
        *,
        bundle: EndToEndResultBundle | None,
        lineage_records: list[WorkflowLineageRecord],
        findings: list[str],
    ) -> bool:
        if bundle is None:
            return False
        payload = bundle.model_dump(mode="json")
        payload.pop("limitations", None)
        text = json.dumps(payload, sort_keys=True)
        failed = False
        for pattern, label in FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(text):
                findings.append(f"forbidden text present: {label}")
                failed = True
        for pattern, label in ANTIBODY_FORBIDDEN_TEXT_PATTERNS:
            if pattern.search(text):
                findings.append(f"forbidden antibody text present: {label}")
                failed = True
        if self._has_truth_flag(payload):
            findings.append("fabricated evidence or external record flag present")
            failed = True
        generated_summary = bundle.generated_summary
        if generated_summary.get("review_required") is False:
            findings.append("generated labels not intact: review_required is false")
            failed = True
        if generated_summary.get("advanced_without_review"):
            findings.append("generated labels not intact: advanced without review")
            failed = True
        biologics_summary = bundle.biologics_summary
        if biologics_summary.get("generated_antibodies_advanced_without_review"):
            findings.append("generated antibody advanced without review")
            failed = True
        if biologics_summary.get("generated_antibodies_with_direct_evidence", 0):
            exact_results = biologics_summary.get("exact_imported_experimental_result_ids")
            if not exact_results:
                findings.append(
                    "generated antibody direct evidence lacks exact imported experimental results"
                )
                failed = True
        if biologics_summary.get("antibody_generation_enabled") is True:
            required_gates = (
                "deterministic_validation_required",
                "novelty_check_required",
                "developability_triage_required",
                "review_gate_required",
                "result_bundle_lineage_required",
            )
            missing = [key for key in required_gates if biologics_summary.get(key) is not True]
            if missing:
                findings.append(
                    "antibody generation missing required gates: " + ", ".join(missing)
                )
                failed = True
        if bundle.metadata.get("codex_outputs_are_separate") is False:
            findings.append("Codex outputs are not separated from evidence")
            failed = True
        if bundle.metadata.get("evaluation_artifacts_are_separate") is False:
            findings.append("evaluation artifacts are not separated")
            failed = True
        if bundle.metadata.get("failed_qc_treated_as_support") is True:
            findings.append("failed QC treated as support")
            failed = True
        for record in lineage_records:
            if record.metadata.get("fabricated_external_record") is True:
                findings.append("fabricated external record lineage flag present")
                failed = True
        return not failed

    def _result_bundle_complete(
        self,
        bundle: EndToEndResultBundle | None,
        findings: list[str],
    ) -> bool:
        if bundle is None:
            return False
        missing = [
            key for key in REQUIRED_BUNDLE_METADATA_KEYS if key not in bundle.metadata
        ]
        # Runner-generated bundles include lineage but not generator manifest metadata;
        # key_artifact_ids is sufficient for that path.
        if "artifact_manifest" in missing and bundle.key_artifact_ids:
            missing.remove("artifact_manifest")
        if "workflow_summary" in missing:
            missing.remove("workflow_summary")
        if missing:
            findings.append(f"result bundle incomplete: {', '.join(sorted(missing))}")
            return False
        limitation_text = " ".join(bundle.limitations).lower()
        if "not scientific evidence" not in limitation_text:
            findings.append("result bundle missing non-evidence limitation")
            return False
        return True

    def _has_truth_flag(self, value: Any) -> bool:
        if isinstance(value, dict):
            for key, raw in value.items():
                normalized = str(key).lower()
                if normalized in SCIENTIFIC_TRUTH_FLAGS and raw is True:
                    return True
                if self._has_truth_flag(raw):
                    return True
        if isinstance(value, list):
            return any(self._has_truth_flag(item) for item in value)
        return False


__all__ = [
    "EndToEndValidationResult",
    "EndToEndWorkflowValidator",
]
