"""Deterministic V3.0 end-to-end workflow eval suite."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from molecule_ranker import __version__
from molecule_ranker.agents.integration_ops import (
    IntegrationOpsAgent,
    IntegrationOpsRequest,
)
from molecule_ranker.e2e.schemas import EndToEndResultBundle
from molecule_ranker.e2e.validation import EndToEndWorkflowValidator
from molecule_ranker.e2e.workflow_runner import (
    EndToEndWorkflowRunner,
    EndToEndWorkflowRunnerConfig,
    WorkflowRunRequest,
)
from molecule_ranker.integrations.schemas import DataContract

E2E_EVAL_CASES: tuple[str, ...] = (
    "mocked_full_discovery_loop",
    "dry_run_integration_sync_loop",
    "partial_failure_with_repair_resume",
    "approval_gate_pause_resume",
    "missing_artifact_detection",
    "external_mapping_conflict",
    "failed_qc_import",
    "generated_exact_result_rule",
    "codex_summary_guardrails",
    "result_bundle_validation",
)

EvalCaseStatus = Literal["passed", "failed", "failed_safely"]


class EndToEndEvalCaseResult(BaseModel):
    """Result for a single deterministic E2E eval case."""

    case_id: str
    name: str
    status: EvalCaseStatus
    workflow_success: bool = False
    partial_recovered: bool | None = None
    approval_gate_recalled: bool | None = None
    lineage_complete: bool = True
    artifact_contract_valid: bool = True
    external_write_escape: bool = False
    generated_overclaim_escape: bool = False
    guardrail_passed: bool = True
    validation_passed: bool | None = None
    time_to_bundle_seconds: float | None = None
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EndToEndEvalMetrics(BaseModel):
    """Aggregate metrics for V3.0 end-to-end evals."""

    workflow_success_rate: float
    partial_recovery_rate: float
    approval_gate_recall: float
    lineage_completeness: float
    artifact_contract_pass_rate: float
    external_write_escape_rate: float
    generated_overclaim_rate: float
    guardrail_pass_rate: float
    time_to_bundle: float


class EndToEndEvalSuiteResult(BaseModel):
    """Report returned by the default V3.0 E2E eval suite."""

    suite: str
    status: Literal["pass", "fail"]
    case_count: int
    case_results: list[EndToEndEvalCaseResult]
    metrics: EndToEndEvalMetrics
    acceptance_passed: bool
    acceptance_failures: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class EndToEndEvalSuite:
    """Runs deterministic workflow and guardrail eval cases for V3.0."""

    def __init__(self, now: Callable[[], datetime] | None = None) -> None:
        self._now = now or (lambda: datetime.now(UTC))
        self._runner = EndToEndWorkflowRunner(now=self._now)
        self._validator = EndToEndWorkflowValidator(now=self._now)
        self._integration_ops = IntegrationOpsAgent(now=self._now)

    def run(self, suite: str = "default") -> EndToEndEvalSuiteResult:
        if suite != "default":
            raise KeyError(f"unknown e2e eval suite: {suite}")
        started_at = self._now()
        case_results = [self.run_case(case_id) for case_id in E2E_EVAL_CASES]
        metrics = self._metrics(case_results)
        acceptance_failures = self._acceptance_failures(case_results, metrics)
        completed_at = self._now()
        return EndToEndEvalSuiteResult(
            suite=suite,
            status="pass" if not acceptance_failures else "fail",
            case_count=len(case_results),
            case_results=case_results,
            metrics=metrics,
            acceptance_passed=not acceptance_failures,
            acceptance_failures=acceptance_failures,
            started_at=started_at,
            completed_at=completed_at,
            metadata={
                "version": __version__,
                "deterministic": True,
                "external_writes_allowed": False,
            },
        )

    def run_case(self, case_id: str) -> EndToEndEvalCaseResult:
        cases: dict[str, Callable[[], EndToEndEvalCaseResult]] = {
            "mocked_full_discovery_loop": self._mocked_full_discovery_loop,
            "dry_run_integration_sync_loop": self._dry_run_integration_sync_loop,
            "partial_failure_with_repair_resume": self._partial_failure_with_repair_resume,
            "approval_gate_pause_resume": self._approval_gate_pause_resume,
            "missing_artifact_detection": self._missing_artifact_detection,
            "external_mapping_conflict": self._external_mapping_conflict,
            "failed_qc_import": self._failed_qc_import,
            "generated_exact_result_rule": self._generated_exact_result_rule,
            "codex_summary_guardrails": self._codex_summary_guardrails,
            "result_bundle_validation": self._result_bundle_validation,
        }
        try:
            return cases[case_id]()
        except KeyError as exc:
            raise KeyError(f"unknown e2e eval case: {case_id}") from exc

    def _mocked_full_discovery_loop(self) -> EndToEndEvalCaseResult:
        started_at = self._now()
        result = self._runner.run(
            WorkflowRunRequest(
                workflow_type="full_discovery_loop",
                mode="mocked",
                disease_name="Deterministic eval disease",
                project_id="eval-project",
                requested_by="e2e-eval",
            )
        )
        validation = self._validator.validate_run_result(result)
        success = (
            result.workflow.status == "succeeded"
            and validation.passed
            and result.external_writes_performed == 0
        )
        return self._case_result(
            case_id="mocked_full_discovery_loop",
            name="Mocked full discovery loop",
            status="passed" if success else "failed",
            workflow_success=success,
            validation_passed=validation.passed,
            lineage_complete=validation.lineage_complete,
            artifact_contract_valid=validation.artifact_contracts_valid,
            guardrail_passed=validation.guardrails_passed,
            external_write_escape=result.external_writes_performed > 0,
            time_to_bundle_seconds=self._elapsed(started_at),
            findings=validation.findings,
            warnings=[*result.warnings, *validation.warnings],
        )

    def _dry_run_integration_sync_loop(self) -> EndToEndEvalCaseResult:
        result = self._runner.run(
            WorkflowRunRequest(
                workflow_type="integration_sync_loop",
                mode="dry_run",
                project_id="eval-project",
                requested_by="e2e-eval",
            )
        )
        validation = self._validator.validate_run_result(result)
        success = (
            result.workflow.status == "succeeded"
            and validation.passed
            and result.planned_external_writes > 0
            and result.external_writes_performed == 0
        )
        return self._case_result(
            case_id="dry_run_integration_sync_loop",
            name="Dry-run integration sync loop",
            status="passed" if success else "failed",
            workflow_success=success,
            validation_passed=validation.passed,
            lineage_complete=validation.lineage_complete,
            artifact_contract_valid=validation.artifact_contracts_valid,
            guardrail_passed=validation.guardrails_passed,
            external_write_escape=result.external_writes_performed > 0,
            findings=validation.findings,
            warnings=[*result.warnings, *validation.warnings],
            metadata={"planned_external_writes": result.planned_external_writes},
        )

    def _partial_failure_with_repair_resume(self) -> EndToEndEvalCaseResult:
        first = self._runner.run(
            WorkflowRunRequest(
                workflow_type="full_discovery_loop",
                mode="read_only_live",
                disease_name="Deterministic eval disease",
                project_id="eval-project",
                requested_by="e2e-eval",
                unavailable_required_data=["generation"],
                config=EndToEndWorkflowRunnerConfig(
                    partial_on_live_data_unavailable=True
                ),
            )
        )
        resumed = self._runner.run(
            WorkflowRunRequest(
                workflow_type="full_discovery_loop",
                mode="read_only_live",
                disease_name="Deterministic eval disease",
                project_id="eval-project",
                requested_by="e2e-eval",
                metadata={
                    "workflow_id": first.workflow.workflow_id,
                    "resumed_from_status": first.workflow.status,
                },
            )
        )
        validation = self._validator.validate_run_result(resumed)
        recovered = first.workflow.status == "partially_succeeded" and (
            resumed.workflow.status == "succeeded" and validation.passed
        )
        return self._case_result(
            case_id="partial_failure_with_repair_resume",
            name="Partial failure with repair/resume",
            status="passed" if recovered else "failed",
            workflow_success=resumed.workflow.status == "succeeded",
            partial_recovered=recovered,
            validation_passed=validation.passed,
            lineage_complete=validation.lineage_complete,
            artifact_contract_valid=validation.artifact_contracts_valid,
            guardrail_passed=validation.guardrails_passed,
            findings=validation.findings,
            warnings=[*first.warnings, *resumed.warnings, *validation.warnings],
            metadata={
                "initial_status": first.workflow.status,
                "resumed_status": resumed.workflow.status,
            },
        )

    def _approval_gate_pause_resume(self) -> EndToEndEvalCaseResult:
        paused = self._runner.run(
            WorkflowRunRequest(
                workflow_type="integration_sync_loop",
                mode="write_approved_live",
                project_id="eval-project",
                requested_by="e2e-eval",
                requested_external_write=True,
            )
        )
        approved = self._runner.run(
            WorkflowRunRequest(
                workflow_type="integration_sync_loop",
                mode="write_approved_live",
                project_id="eval-project",
                requested_by="e2e-eval",
                requested_external_write=True,
                approvals=["external_write"],
                governance_permissions=["integration:write"],
                metadata={
                    "workflow_id": paused.workflow.workflow_id,
                    "approval_ids": ["external_write"],
                },
            )
        )
        gate_recalled = paused.workflow.status == "awaiting_approval"
        validation = self._validator.validate_run_result(approved)
        success = gate_recalled and approved.workflow.status == "succeeded"
        approved_ids = (
            approved.bundle.metadata.get("approval_ids", [])
            if approved.bundle is not None
            else []
        )
        external_write_escape = paused.external_writes_performed > 0 or (
            approved.external_writes_performed > 0
            and "external_write" not in approved_ids
        )
        return self._case_result(
            case_id="approval_gate_pause_resume",
            name="Approval gate pause/resume",
            status="passed" if success else "failed",
            workflow_success=approved.workflow.status == "succeeded",
            approval_gate_recalled=gate_recalled,
            validation_passed=validation.passed,
            lineage_complete=validation.lineage_complete,
            artifact_contract_valid=validation.artifact_contracts_valid,
            guardrail_passed=validation.guardrails_passed,
            external_write_escape=external_write_escape,
            findings=validation.findings,
            warnings=[*paused.warnings, *approved.warnings, *validation.warnings],
            metadata={
                "paused_status": paused.workflow.status,
                "approved_status": approved.workflow.status,
                "approved_external_writes": approved.external_writes_performed,
            },
        )

    def _missing_artifact_detection(self) -> EndToEndEvalCaseResult:
        result = self._mocked_run()
        bundle = self._require_bundle(result.bundle).model_copy(deep=True)
        bundle.key_artifact_ids = bundle.key_artifact_ids[:-1]
        validation = self._validator.validate(
            workflow=result.workflow,
            steps=result.steps,
            bundle=bundle,
            lineage_records=result.lineage_records,
        )
        detected = not validation.passed and any(
            "required artifacts missing" in finding
            for finding in validation.findings
        )
        return self._case_result(
            case_id="missing_artifact_detection",
            name="Missing artifact detection",
            status="failed_safely" if detected else "failed",
            workflow_success=False,
            validation_passed=validation.passed,
            lineage_complete=validation.lineage_complete,
            artifact_contract_valid=validation.artifact_contracts_valid,
            guardrail_passed=detected,
            findings=validation.findings,
            warnings=validation.warnings,
        )

    def _external_mapping_conflict(self) -> EndToEndEvalCaseResult:
        result = self._integration_ops.detect_mapping_conflicts(
            internal_entity={"candidate_id": "candidate-eval-1", "name": "Eval candidate"},
            external_records=[
                {
                    "external_system_id": "registry-eval",
                    "external_record_id": "external-candidate-1",
                    "name": "Eval candidate",
                },
                {
                    "external_system_id": "registry-eval",
                    "external_record_id": "external-candidate-2",
                    "name": "Eval candidate",
                },
            ],
            internal_entity_type="candidate",
            project_id="eval-project",
            codex_suggested=True,
        )
        contained = result.status == "pending_review" and bool(result.mapping_review_queue)
        return self._case_result(
            case_id="external_mapping_conflict",
            name="External mapping conflict",
            status="failed_safely" if contained else "failed",
            workflow_success=False,
            guardrail_passed=contained,
            external_write_escape=result.external_write_performed,
            warnings=result.warnings,
            metadata={
                "mapping_status": result.mapping.status if result.mapping else None,
                "mapping_review_queue_size": len(result.mapping_review_queue),
            },
        )

    def _failed_qc_import(self) -> EndToEndEvalCaseResult:
        result = self._integration_ops.import_validated_assay_results(
            IntegrationOpsRequest(
                project_id="eval-project",
                external_system_id="lims-eval",
                object_types=["assay_result"],
                data_contract=self._assay_result_contract(),
                assay_results=[
                    {
                        "result_id": "assay-eval-1",
                        "candidate_id": "candidate-eval-1",
                        "assay_name": "Deterministic fixture assay",
                        "source_record_id": "external-assay-1",
                    }
                ],
            )
        )
        blocked = (
            result.status == "validation_failed"
            and result.records_failed > 0
            and result.records_imported == 0
            and not result.external_write_performed
        )
        return self._case_result(
            case_id="failed_qc_import",
            name="Failed QC import",
            status="failed_safely" if blocked else "failed",
            workflow_success=False,
            guardrail_passed=blocked,
            external_write_escape=result.external_write_performed,
            warnings=result.warnings,
            metadata={
                "records_failed": result.records_failed,
                "records_imported": result.records_imported,
            },
        )

    def _generated_exact_result_rule(self) -> EndToEndEvalCaseResult:
        result = self._mocked_run()
        bundle = self._require_bundle(result.bundle).model_copy(deep=True)
        bundle.generated_summary["review_required"] = False
        bundle.generated_summary["advanced_without_review"] = True
        validation = self._validator.validate(
            workflow=result.workflow,
            steps=result.steps,
            bundle=bundle,
            lineage_records=result.lineage_records,
        )
        detected = not validation.passed and any(
            "generated labels not intact" in finding
            for finding in validation.findings
        )
        return self._case_result(
            case_id="generated_exact_result_rule",
            name="Generated exact-result rule",
            status="failed_safely" if detected else "failed",
            workflow_success=False,
            validation_passed=validation.passed,
            lineage_complete=validation.lineage_complete,
            artifact_contract_valid=validation.artifact_contracts_valid,
            generated_overclaim_escape=not detected,
            guardrail_passed=detected,
            findings=validation.findings,
            warnings=validation.warnings,
        )

    def _codex_summary_guardrails(self) -> EndToEndEvalCaseResult:
        result = self._mocked_run()
        bundle = self._require_bundle(result.bundle).model_copy(deep=True)
        bundle.metadata["codex_outputs_are_separate"] = False
        validation = self._validator.validate(
            workflow=result.workflow,
            steps=result.steps,
            bundle=bundle,
            lineage_records=result.lineage_records,
        )
        detected = not validation.passed and any(
            "Codex outputs are not separated" in finding
            for finding in validation.findings
        )
        return self._case_result(
            case_id="codex_summary_guardrails",
            name="Codex summary guardrails",
            status="failed_safely" if detected else "failed",
            workflow_success=False,
            validation_passed=validation.passed,
            lineage_complete=validation.lineage_complete,
            artifact_contract_valid=validation.artifact_contracts_valid,
            guardrail_passed=detected,
            findings=validation.findings,
            warnings=validation.warnings,
        )

    def _result_bundle_validation(self) -> EndToEndEvalCaseResult:
        result = self._mocked_run()
        validation = self._validator.validate_run_result(result)
        success = result.bundle is not None and validation.passed
        return self._case_result(
            case_id="result_bundle_validation",
            name="Result bundle validation",
            status="passed" if success else "failed",
            workflow_success=success,
            validation_passed=validation.passed,
            lineage_complete=validation.lineage_complete,
            artifact_contract_valid=validation.artifact_contracts_valid,
            guardrail_passed=validation.guardrails_passed,
            findings=validation.findings,
            warnings=validation.warnings,
            metadata={"bundle_id": result.bundle.bundle_id if result.bundle else None},
        )

    def _mocked_run(self):
        return self._runner.run(
            WorkflowRunRequest(
                workflow_type="full_discovery_loop",
                mode="mocked",
                disease_name="Deterministic eval disease",
                project_id="eval-project",
                requested_by="e2e-eval",
            )
        )

    def _assay_result_contract(self) -> DataContract:
        return DataContract(
            contract_id="eval-assay-contract-v2",
            name="Eval assay result",
            object_type="assay_result",
            version="V2",
            required_fields=[
                "result_id",
                "candidate_id",
                "assay_name",
                "measured_value",
                "measured_unit",
                "source_record_id",
            ],
            field_types={
                "result_id": "string",
                "candidate_id": "string",
                "assay_name": "string",
                "measured_value": "number",
                "measured_unit": "string",
                "source_record_id": "string",
            },
            identifier_fields=["result_id", "candidate_id", "source_record_id"],
        )

    def _case_result(
        self,
        *,
        case_id: str,
        name: str,
        status: EvalCaseStatus,
        workflow_success: bool,
        partial_recovered: bool | None = None,
        approval_gate_recalled: bool | None = None,
        lineage_complete: bool = True,
        artifact_contract_valid: bool = True,
        external_write_escape: bool = False,
        generated_overclaim_escape: bool = False,
        guardrail_passed: bool = True,
        validation_passed: bool | None = None,
        time_to_bundle_seconds: float | None = None,
        findings: list[str] | None = None,
        warnings: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EndToEndEvalCaseResult:
        return EndToEndEvalCaseResult(
            case_id=case_id,
            name=name,
            status=status,
            workflow_success=workflow_success,
            partial_recovered=partial_recovered,
            approval_gate_recalled=approval_gate_recalled,
            lineage_complete=lineage_complete,
            artifact_contract_valid=artifact_contract_valid,
            external_write_escape=external_write_escape,
            generated_overclaim_escape=generated_overclaim_escape,
            guardrail_passed=guardrail_passed,
            validation_passed=validation_passed,
            time_to_bundle_seconds=time_to_bundle_seconds,
            findings=findings or [],
            warnings=warnings or [],
            metadata=metadata or {},
        )

    def _metrics(
        self, case_results: list[EndToEndEvalCaseResult]
    ) -> EndToEndEvalMetrics:
        count = len(case_results)
        partial_cases = [
            result for result in case_results if result.partial_recovered is not None
        ]
        approval_cases = [
            result for result in case_results if result.approval_gate_recalled is not None
        ]
        times = [
            result.time_to_bundle_seconds
            for result in case_results
            if result.time_to_bundle_seconds is not None
        ]
        return EndToEndEvalMetrics(
            workflow_success_rate=self._rate(
                result.workflow_success for result in case_results
            ),
            partial_recovery_rate=self._rate(
                result.partial_recovered is True for result in partial_cases
            ),
            approval_gate_recall=self._rate(
                result.approval_gate_recalled is True for result in approval_cases
            ),
            lineage_completeness=self._rate(
                result.lineage_complete for result in case_results
            ),
            artifact_contract_pass_rate=self._rate(
                result.artifact_contract_valid for result in case_results
            ),
            external_write_escape_rate=(
                sum(1 for result in case_results if result.external_write_escape) / count
                if count
                else 0.0
            ),
            generated_overclaim_rate=(
                sum(
                    1
                    for result in case_results
                    if result.generated_overclaim_escape
                )
                / count
                if count
                else 0.0
            ),
            guardrail_pass_rate=self._rate(
                result.guardrail_passed for result in case_results
            ),
            time_to_bundle=(sum(times) / len(times) if times else 0.0),
        )

    def _acceptance_failures(
        self,
        case_results: list[EndToEndEvalCaseResult],
        metrics: EndToEndEvalMetrics,
    ) -> list[str]:
        failures: list[str] = []
        mocked = next(
            result
            for result in case_results
            if result.case_id == "mocked_full_discovery_loop"
        )
        if mocked.status != "passed":
            failures.append("mocked full discovery loop did not pass")
        if metrics.external_write_escape_rate != 0:
            failures.append("external write escape rate must be zero")
        if metrics.generated_overclaim_rate != 0:
            failures.append("generated overclaim rate must be zero")
        return failures

    def _elapsed(self, started_at: datetime) -> float:
        return max((self._now() - started_at).total_seconds(), 0.0)

    def _rate(self, values: Any) -> float:
        materialized = list(values)
        if not materialized:
            return 1.0
        return sum(1 for value in materialized if value) / len(materialized)

    def _require_bundle(
        self, bundle: EndToEndResultBundle | None
    ) -> EndToEndResultBundle:
        if bundle is None:
            raise ValueError("eval fixture expected a result bundle")
        return bundle


def run_end_to_end_eval_suite(suite: str = "default") -> EndToEndEvalSuiteResult:
    """Run the deterministic V3.0 end-to-end eval suite."""

    return EndToEndEvalSuite().run(suite=suite)


__all__ = [
    "E2E_EVAL_CASES",
    "EndToEndEvalCaseResult",
    "EndToEndEvalMetrics",
    "EndToEndEvalSuite",
    "EndToEndEvalSuiteResult",
    "run_end_to_end_eval_suite",
]
