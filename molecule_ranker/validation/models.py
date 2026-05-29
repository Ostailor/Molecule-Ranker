from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.design.oracles import MultiObjectiveOracleStack
from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.experiments.store import ExperimentalResultStore
from molecule_ranker.generation.schemas import (
    ChemicalValidationResult,
    GeneratedMolecule,
    GenerationObjective,
    NoveltyAssessment,
    SeedMolecule,
)
from molecule_ranker.models.calibration import calibrate_classifier_probabilities
from molecule_ranker.models.datasets import build_assay_model_training_dataset
from molecule_ranker.models.reports import write_model_report_artifacts
from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEndpoint,
    ModelEvaluationReport,
    ModelFeatureSpec,
    ModelPrediction,
    ModelTrainingDataset,
    ModelTrainingRun,
)
from molecule_ranker.models.training import train_baseline_surrogate_model
from molecule_ranker.validation.reports import write_json_artifact, write_markdown_artifact

ModelValidationStatus = Literal["pass", "fail"]
ModelValidationFixture = Literal[
    "golden",
    "leakage",
    "uncalibrated_overclaim",
    "fake_prediction_evidence",
]

MODEL_VALIDATION_STEPS = [
    "synthetic assay results imported",
    "endpoint-specific dataset built",
    "baseline model trained",
    "evaluation report includes leakage checks",
    "calibration computed when enough validation data exist",
    "existing and generated candidate predictions written",
    "calibrated surrogate predictions integrated into oracle scoring",
    "model reports generated",
    "model guardrails verified",
]

MODEL_GUARDRAIL_CATEGORIES = (
    "Prediction/evidence separation",
    "Assay-result integrity",
    "Claim safety",
    "Generated molecule integrity",
    "Leakage and calibration",
    "Applicability domain",
)


@dataclass(frozen=True)
class ModelGuardrailFinding:
    category: str
    check_id: str
    severity: str
    artifact_path: str
    message: str
    excerpt: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "check_id": self.check_id,
            "severity": self.severity,
            "artifact_path": self.artifact_path,
            "message": self.message,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class ModelGuardrailAuditReport:
    status: ModelValidationStatus
    root_dir: Path
    artifact_count: int
    categories: tuple[str, ...]
    findings: list[ModelGuardrailFinding]

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "root_dir": str(self.root_dir),
            "artifact_count": self.artifact_count,
            "categories": list(self.categories),
            "finding_count": len(self.findings),
            "findings": [finding.as_dict() for finding in self.findings],
        }


@dataclass(frozen=True)
class ModelValidationReport:
    status: ModelValidationStatus
    output_dir: Path
    fixture: str
    artifacts: list[str]
    required_steps: list[str]
    guardrail_audit: ModelGuardrailAuditReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
            "fixture": self.fixture,
            "artifacts": self.artifacts,
            "required_steps": self.required_steps,
            "guardrail_audit": self.guardrail_audit.as_dict(),
        }


def run_model_validation(
    *,
    output_dir: str | Path = ".molecule-ranker/validation/models",
    fixture: ModelValidationFixture = "golden",
) -> ModelValidationReport:
    """Run the deterministic V1.2 predictive-model validation workflow."""

    resolved_output = Path(output_dir).resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    workflow = _write_model_validation_workflow(resolved_output, fixture=fixture)
    audit = run_model_guardrail_audit(resolved_output)
    artifacts = sorted(
        str(path.relative_to(resolved_output))
        for path in resolved_output.rglob("*")
        if path.is_file()
    )
    report = ModelValidationReport(
        status="pass" if audit.status == "pass" else "fail",
        output_dir=resolved_output,
        fixture=fixture,
        artifacts=artifacts,
        required_steps=MODEL_VALIDATION_STEPS,
        guardrail_audit=audit,
    )
    write_json_artifact(resolved_output / "model_validation_report.json", report.as_dict())
    write_markdown_artifact(
        resolved_output / "model_validation_report.md",
        "V1.2 Model Validation Report",
        [
            f"- Status: `{report.status}`",
            f"- Fixture: `{fixture}`",
            f"- Dataset ID: `{workflow['dataset'].dataset_id}`",
            f"- Model ID: `{workflow['model_card'].model_id}`",
            f"- Guardrail findings: {len(audit.findings)}",
            "",
            "## Required Steps",
            *[f"- {step}" for step in report.required_steps],
        ],
    )
    return report


def run_model_guardrail_audit(path: str | Path) -> ModelGuardrailAuditReport:
    root = Path(path).resolve()
    artifacts = [item for item in root.rglob("*") if item.is_file()]
    payloads = _json_payloads(root)
    findings: list[ModelGuardrailFinding] = []

    dataset = _load_model(payloads, "model_training_dataset.json", ModelTrainingDataset)
    evaluation = _load_model(payloads, "model_evaluation_report.json", ModelEvaluationReport)
    predictions = _load_predictions(payloads)
    report_texts = _report_texts(root)

    findings.extend(_prediction_guardrail_findings(predictions))
    findings.extend(_claim_guardrail_findings(report_texts))
    findings.extend(_generated_linkage_findings(dataset, predictions))
    findings.extend(_leakage_findings(evaluation))
    findings.extend(_calibration_findings(predictions))
    findings.extend(_applicability_findings(predictions))

    report = ModelGuardrailAuditReport(
        status="fail" if findings else "pass",
        root_dir=root,
        artifact_count=len(artifacts),
        categories=MODEL_GUARDRAIL_CATEGORIES,
        findings=findings,
    )
    _write_model_guardrail_audit_reports(report)
    return report


def _write_model_validation_workflow(
    output_dir: Path,
    *,
    fixture: ModelValidationFixture,
) -> dict[str, Any]:
    endpoint = _validation_endpoint()
    feature_spec = _validation_feature_spec()
    store = ExperimentalResultStore(output_dir / "synthetic_assay_results.sqlite")
    imported_results = _synthetic_assay_results(endpoint)
    store.import_results(imported_results, update=True)
    candidates = [_candidate_payload(result) for result in imported_results]
    generated_candidates = [
        _generated_candidate_payload("GEN-NEAR"),
        _generated_candidate_payload("GEN-OOD"),
    ]

    dataset_result = build_assay_model_training_dataset(
        store,
        candidates=candidates,
        generated_molecules=generated_candidates,
        endpoint=endpoint,
        feature_spec=feature_spec,
        output_dir=output_dir / "dataset",
        config={},
    )
    feature_rows = [_training_row(row, result) for row, result in zip(
        dataset_result.features,
        imported_results,
        strict=True,
    )]
    training_result = train_baseline_surrogate_model(
        dataset=dataset_result.dataset,
        feature_rows=feature_rows,
        labels=dataset_result.labels,
        output_dir=output_dir / "training",
        config={
            "model_id": "model-validation-baseline",
            "model_type": "dummy",
            "split_strategy": "scaffold",
            "random_seed": 17,
            "min_training_rows_binary": 8,
            "min_positive_count": 2,
            "min_negative_count": 2,
        },
    )
    if training_result.model_card is None or training_result.split_result is None:
        raise RuntimeError("Model validation training did not produce a model card.")

    calibration = calibrate_classifier_probabilities(
        [0.82 if label else 0.18 for label in dataset_result.labels],
        dataset_result.labels,
        config={"min_calibration_rows": 6, "n_bins": 4},
    )
    calibration_metrics = {
        "status": calibration.calibration_status,
        **calibration.metrics,
        **calibration.metadata,
    }
    model_card = training_result.model_card.model_copy(
        update={"calibration_metrics": calibration_metrics}
    )
    training_run = training_result.training_run.model_copy(
        update={"calibration_metrics": calibration_metrics}
    )
    evaluation = ModelEvaluationReport(
        evaluation_id="model-validation-evaluation",
        model_id=model_card.model_id,
        dataset_id=dataset_result.dataset.dataset_id,
        split_strategy=training_result.split_result.strategy,
        metrics=training_run.metrics,
        calibration_metrics=calibration_metrics,
        leakage_checks=training_result.split_result.leakage_check_report,
        applicability_domain_summary={
            "method": "synthetic_validation_domain_checks",
            "in_domain": 1,
            "near_domain": 1,
            "out_of_domain": 1,
            "unknown": 0,
        },
        warnings=[],
        metadata={
            "evaluation_uses_synthetic_validation_fixture": True,
            "not_experimental_evidence": True,
        },
    )
    predictions = _validation_predictions(
        model_card,
        calibration_status=calibration.calibration_status,
    )
    if fixture == "leakage":
        evaluation = evaluation.model_copy(
            update={
                "leakage_checks": {
                    "passed": False,
                    "failed_checks": ["duplicate_inchi_key"],
                    "checks": {
                        "duplicate_inchi_key": {
                            "passed": False,
                            "values": ["SYNTHETIC-INCHI-KEY"],
                        }
                    },
                }
            }
        )
    elif fixture == "uncalibrated_overclaim":
        predictions = [
            prediction.model_copy(
                update={
                    "calibration_status": "uncalibrated",
                    "metadata": {
                        **prediction.metadata,
                        "display_calibration_status": "calibrated",
                    },
                }
            )
            for prediction in predictions
        ]
    elif fixture == "fake_prediction_evidence":
        predictions = [
            predictions[0].model_copy(
                update={
                    "metadata": {
                        **predictions[0].metadata,
                        "as_evidence_item": True,
                        "as_assay_result": True,
                    }
                }
            ),
            *predictions[1:],
        ]

    oracle_score = _write_oracle_score(output_dir, endpoint=endpoint, prediction=predictions[1])
    report_paths = write_model_report_artifacts(
        output_dir=output_dir / "reports",
        dataset=dataset_result.dataset,
        training_run=training_run,
        model_card=model_card,
        predictions=predictions,
        split_result=training_result.split_result,
        evaluation_report=evaluation,
        prediction_batch_artifact_id="model_predictions.json",
    )
    _write_core_validation_artifacts(
        output_dir=output_dir,
        dataset=dataset_result.dataset,
        training_run=training_run,
        model_card=model_card,
        evaluation=evaluation,
        predictions=predictions,
        calibration_metrics=calibration_metrics,
        oracle_score=oracle_score,
        report_paths=report_paths,
        imported_results=imported_results,
    )
    return {
        "dataset": dataset_result.dataset,
        "training_run": training_run,
        "model_card": model_card,
        "evaluation": evaluation,
        "predictions": predictions,
    }


def _write_core_validation_artifacts(
    *,
    output_dir: Path,
    dataset: ModelTrainingDataset,
    training_run: ModelTrainingRun,
    model_card: ModelCard,
    evaluation: ModelEvaluationReport,
    predictions: Sequence[ModelPrediction],
    calibration_metrics: Mapping[str, Any],
    oracle_score: Mapping[str, Any],
    report_paths: Mapping[str, Path],
    imported_results: Sequence[AssayResult],
) -> None:
    write_json_artifact(
        output_dir / "synthetic_assay_results.json",
        {
            "artifact_type": "SyntheticAssayResultImport",
            "source_system": "synthetic_validation_fixture",
            "result_count": len(imported_results),
            "results": [result.model_dump(mode="json") for result in imported_results],
            "not_biomedical_evidence": True,
        },
    )
    write_json_artifact(output_dir / "model_training_dataset.json", dataset.model_dump(mode="json"))
    write_json_artifact(
        output_dir / "model_training_run.json",
        training_run.model_dump(mode="json"),
    )
    write_json_artifact(output_dir / "model_card.json", model_card.model_dump(mode="json"))
    write_json_artifact(
        output_dir / "model_evaluation_report.json",
        evaluation.model_dump(mode="json"),
    )
    write_json_artifact(
        output_dir / "calibration_summary.json",
        {
            "model_id": model_card.model_id,
            "dataset_id": dataset.dataset_id,
            "training_run_id": training_run.training_run_id,
            "evaluation_id": evaluation.evaluation_id,
            "calibration_metrics": dict(calibration_metrics),
            "not_experimental_evidence": True,
            "not_assay_result": True,
        },
    )
    write_json_artifact(
        output_dir / "model_predictions.json",
        {
            "artifact_type": "ModelPredictionArtifact",
            "prediction_batch_artifact_id": "model_predictions.json",
            "model_id": model_card.model_id,
            "dataset_id": dataset.dataset_id,
            "training_run_id": training_run.training_run_id,
            "evaluation_id": evaluation.evaluation_id,
            "endpoint_id": model_card.endpoint.endpoint_id,
            "predictions": [prediction.model_dump(mode="json") for prediction in predictions],
            "warnings": [
                "Predictions are not experimental evidence.",
                "Predictions are not assay results.",
                "Generated molecules remain computational hypotheses.",
            ],
        },
    )
    write_json_artifact(output_dir / "oracle_scores.json", dict(oracle_score))
    write_json_artifact(
        output_dir / "model_report_manifest.json",
        {key: str(path) for key, path in report_paths.items()},
    )


def _write_oracle_score(
    output_dir: Path,
    *,
    endpoint: ModelEndpoint,
    prediction: ModelPrediction,
) -> dict[str, Any]:
    generated = _generated_molecule(
        "GEN-NEAR",
        metadata={
            "model_predictions": [_oracle_prediction_payload(prediction)],
            "synthetic_accessibility_score": 0.7,
            "developability_score": 0.7,
        },
    )
    objective = GenerationObjective(
        objective_id="model-validation-objective",
        disease_name=endpoint.disease_name or "synthetic disease context",
        target_symbol=endpoint.target_symbol or "SYN1",
        objective_type="target_conditioned_analog_generation",
        seed_molecule_names=["Synthetic seed 1"],
        seed_molecule_ids=["CAND-1"],
        metadata={
            "target_relevance_score": 0.8,
            "model_endpoint_id": endpoint.endpoint_id,
        },
    )
    seed = SeedMolecule(
        name="Synthetic seed 1",
        canonical_smiles="CCOc1ccccc1",
        identifiers={"synthetic": "CAND-1"},
        known_targets=[endpoint.target_symbol or "SYN1"],
        source_candidate_name="Synthetic Candidate 1",
        evidence_count=1,
        best_evidence_confidence=0.8,
        target_relevance_score=0.8,
        seed_selection_reason="Synthetic validation seed with imported assay result.",
        metadata={"scaffold_id": "seed-scaffold", "literature_support_score": 0.4},
    )
    oracle = MultiObjectiveOracleStack().score(
        candidate=generated,
        objective=objective,
        seeds=[seed],
        retained_generated=[],
        enable_surrogate_oracle=True,
        surrogate_oracle_weight=0.08,
        require_calibrated_predictions=True,
        min_prediction_confidence=0.5,
        out_of_domain_penalty=0.08,
        surrogate_endpoint_id=endpoint.endpoint_id,
    )
    payload = oracle.model_dump(mode="json")
    payload["not_experimental_evidence"] = True
    payload["not_assay_result"] = True
    (output_dir / "oracle_scores.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    return payload


def _prediction_guardrail_findings(
    predictions: Sequence[ModelPrediction],
) -> list[ModelGuardrailFinding]:
    findings: list[ModelGuardrailFinding] = []
    for prediction in predictions:
        metadata = prediction.metadata
        if metadata.get("as_evidence_item") is True or metadata.get("not_evidence_item") is False:
            findings.append(
                _finding(
                    "Prediction/evidence separation",
                    "predictions_are_not_evidence_items",
                    "Prediction artifact is represented as an EvidenceItem.",
                    prediction.prediction_id,
                )
            )
        if (
            metadata.get("as_assay_result") is True
            or metadata.get("not_assay_result") is False
        ):
            findings.append(
                _finding(
                    "Assay-result integrity",
                    "predictions_are_not_assay_results",
                    "Prediction artifact is represented as an assay result.",
                    prediction.prediction_id,
                )
            )
    return findings


def _claim_guardrail_findings(
    report_texts: Mapping[str, str],
) -> list[ModelGuardrailFinding]:
    findings: list[ModelGuardrailFinding] = []
    activity_patterns = [
        re.compile(r"\b(?:validated|confirmed|known)\s+active\b", re.I),
        re.compile(r"\bactivity\s+evidence\b", re.I),
        re.compile(r"\bprediction\s+(?:proves|confirms|validates)\b", re.I),
    ]
    clinical_patterns = [
        re.compile(r"\bclinical\s+(?:use|recommendation|benefit)\b", re.I),
        re.compile(r"\b(?:treats|treatment|cures|efficacy|patient|dosing)\b", re.I),
    ]
    for artifact_path, text in report_texts.items():
        for pattern in activity_patterns:
            match = pattern.search(text)
            if match:
                findings.append(
                    _finding(
                        "Claim safety",
                        "no_activity_claims_without_imported_result_evidence",
                        "Model validation artifact contains an unsupported activity claim.",
                        artifact_path,
                        match.group(0),
                    )
                )
                break
        for pattern in clinical_patterns:
            match = pattern.search(text)
            if match:
                findings.append(
                    _finding(
                        "Claim safety",
                        "no_clinical_claims",
                        "Model validation artifact contains a clinical or dosing claim.",
                        artifact_path,
                        match.group(0),
                    )
                )
                break
    return findings


def _generated_linkage_findings(
    dataset: ModelTrainingDataset | None,
    predictions: Sequence[ModelPrediction],
) -> list[ModelGuardrailFinding]:
    findings: list[ModelGuardrailFinding] = []
    seed_guardrail = (
        dataset.metadata.get("seed_results_not_used_for_generated_analogs")
        if dataset is not None
        else None
    )
    if dataset is not None and seed_guardrail is not True:
        findings.append(
            _finding(
                "Generated molecule integrity",
                "no_generated_molecule_validation_from_seed_result",
                "Dataset manifest does not preserve the seed-result guardrail.",
                "model_training_dataset.json",
            )
        )
    for prediction in predictions:
        if (
            prediction.candidate_origin == "generated"
            and prediction.metadata.get("labeled_from_seed_result") is True
        ):
            findings.append(
                _finding(
                    "Generated molecule integrity",
                    "no_generated_molecule_validation_from_seed_result",
                    "Generated molecule prediction is labeled from a seed result.",
                    prediction.prediction_id,
                )
            )
    return findings


def _leakage_findings(
    evaluation: ModelEvaluationReport | None,
) -> list[ModelGuardrailFinding]:
    if evaluation is None:
        return [
            _finding(
                "Leakage and calibration",
                "no_leakage",
                "Evaluation report with leakage checks is missing.",
                "model_evaluation_report.json",
            )
        ]
    leakage = evaluation.leakage_checks
    if leakage.get("passed") is not True:
        failed = ", ".join(str(item) for item in leakage.get("failed_checks", [])) or "unknown"
        return [
            _finding(
                "Leakage and calibration",
                "no_leakage",
                f"Leakage checks failed: {failed}.",
                "model_evaluation_report.json",
            )
        ]
    return []


def _calibration_findings(
    predictions: Sequence[ModelPrediction],
) -> list[ModelGuardrailFinding]:
    findings: list[ModelGuardrailFinding] = []
    for prediction in predictions:
        displayed = str(prediction.metadata.get("display_calibration_status") or "")
        if prediction.calibration_status != "calibrated" and displayed == "calibrated":
            findings.append(
                _finding(
                    "Leakage and calibration",
                    "no_uncalibrated_prediction_shown_as_calibrated",
                    "Uncalibrated prediction is displayed as calibrated.",
                    prediction.prediction_id,
                )
            )
    return findings


def _applicability_findings(
    predictions: Sequence[ModelPrediction],
) -> list[ModelGuardrailFinding]:
    findings: list[ModelGuardrailFinding] = []
    for prediction in predictions:
        if prediction.applicability_domain == "out_of_domain" and prediction.confidence > 0.5:
            findings.append(
                _finding(
                    "Applicability domain",
                    "no_out_of_domain_prediction_high_confidence",
                    "Out-of-domain prediction has high confidence.",
                    prediction.prediction_id,
                )
            )
    return findings


def _write_model_guardrail_audit_reports(report: ModelGuardrailAuditReport) -> None:
    report.root_dir.mkdir(parents=True, exist_ok=True)
    write_json_artifact(report.root_dir / "model_guardrail_audit.json", report.as_dict())
    lines = [
        f"- Status: `{report.status}`",
        f"- Artifacts: {report.artifact_count}",
        f"- Findings: {len(report.findings)}",
        "",
        "## Findings",
    ]
    if not report.findings:
        lines.append("- None")
    else:
        for finding in report.findings:
            lines.append(
                f"- `{finding.check_id}` ({finding.severity}) in `{finding.artifact_path}`: "
                f"{finding.message}"
            )
    write_markdown_artifact(
        report.root_dir / "model_guardrail_audit.md",
        "V1.2 Model Guardrail Audit",
        lines,
    )


def _validation_endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        endpoint_id="endpoint-model-validation-maob",
        endpoint_name="synthetic_maob_activity",
        endpoint_category="potency",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        assay_type="biochemical",
        unit=None,
        label_type="binary",
        positive_label="positive",
        directionality="binary",
        thresholds={"positive_label": "positive", "negative_label": "negative"},
        metadata={"source": "synthetic_validation_fixture"},
    )


def _validation_feature_spec() -> ModelFeatureSpec:
    return ModelFeatureSpec(
        feature_spec_id="feature-spec-model-validation",
        feature_families=["rdkit_descriptors", "target_context"],
        fingerprint_radius=None,
        fingerprint_bits=None,
        descriptor_names=["molecular_weight", "logp", "tpsa"],
        normalization="none",
        metadata={"deterministic_validation": True},
    )


def _synthetic_assay_results(endpoint: ModelEndpoint) -> list[AssayResult]:
    assay_endpoint = AssayEndpoint(
        endpoint_id=endpoint.endpoint_id,
        name=endpoint.endpoint_name,
        endpoint_category=endpoint.endpoint_category,
        directionality=endpoint.directionality,
    )
    context = AssayContext(
        assay_context_id="context-model-validation-maob",
        assay_name="Synthetic MAOB validation assay",
        assay_type="biochemical",
        target_symbol=endpoint.target_symbol,
        disease_name=endpoint.disease_name,
        endpoint=assay_endpoint,
    )
    smiles = [
        "CCOc1ccccc1",
        "CCN(CC)CCO",
        "c1ccncc1",
        "CC(C)Oc1ccccc1",
        "COc1ccccc1",
        "CCOC(=O)c1ccccc1",
        "CC(C)NCCO",
        "CCSc1ccccc1",
    ]
    results: list[AssayResult] = []
    for index, canonical_smiles in enumerate(smiles, start=1):
        positive = index % 2 == 1
        results.append(
            AssayResult(
                result_id=f"SYN-ASSAY-{index:03d}",
                candidate_id=f"CAND-{index}",
                candidate_name=f"Synthetic Candidate {index}",
                candidate_origin="existing",
                canonical_smiles=canonical_smiles,
                inchi_key=f"SYN-INCHI-{index}",
                disease_name=endpoint.disease_name,
                target_symbol=endpoint.target_symbol,
                assay_context=context,
                normalized_value=float(index),
                outcome_label="positive" if positive else "negative",
                activity_direction="active" if positive else "inactive",
                confidence=0.8,
                qc_status="passed",
                source="synthetic_validation_fixture",
                imported_at=datetime.now(UTC),
                metadata={
                    "source_system": "synthetic_validation_fixture",
                    "exact_candidate_linkage": True,
                    "not_clinical_data": True,
                },
            )
        )
    return results


def _validation_predictions(
    model_card: ModelCard,
    *,
    calibration_status: str,
) -> list[ModelPrediction]:
    now = datetime.now(UTC)
    common = {
        "model_id": model_card.model_id,
        "model_version": model_card.model_version,
        "endpoint_id": model_card.endpoint.endpoint_id,
        "calibration_status": calibration_status,
        "created_at": now,
    }
    return [
        ModelPrediction(
            prediction_id="PRED-EXISTING-001",
            candidate_id="CAND-1",
            candidate_name="Synthetic Candidate 1",
            candidate_origin="existing",
            canonical_smiles="CCOc1ccccc1",
            inchi_key="SYN-INCHI-1",
            predicted_value=True,
            predicted_probability=0.72,
            prediction_label="surrogate_positive",
            uncertainty=0.22,
            confidence=0.78,
            applicability_domain="in_domain",
            explanation="Calibrated surrogate prediction artifact for validation only.",
            warnings=[
                "Prediction is not experimental evidence.",
                "Prediction is not an assay result.",
            ],
            metadata=_prediction_metadata(),
            **common,
        ),
        ModelPrediction(
            prediction_id="PRED-GENERATED-NEAR-001",
            candidate_id="GEN-NEAR",
            candidate_name="GEN-NEAR",
            candidate_origin="generated",
            canonical_smiles="CCOc1ccccc1C",
            inchi_key="SYN-GEN-NEAR",
            predicted_value=True,
            predicted_probability=0.66,
            prediction_label="surrogate_positive",
            uncertainty=0.38,
            confidence=0.62,
            applicability_domain="near_domain",
            explanation="Generated molecule remains a computational hypothesis.",
            warnings=[
                "Generated molecule prediction is not evidence.",
                "Generated molecule requires exact imported assay result evidence.",
            ],
            metadata=_prediction_metadata(generated=True),
            **common,
        ),
        ModelPrediction(
            prediction_id="PRED-GENERATED-OOD-001",
            candidate_id="GEN-OOD",
            candidate_name="GEN-OOD",
            candidate_origin="generated",
            canonical_smiles="CCCCCCCCCCCCCCCC",
            inchi_key="SYN-GEN-OOD",
            predicted_value=None,
            predicted_probability=0.51,
            prediction_label="surrogate_uncertain",
            uncertainty=0.74,
            confidence=0.26,
            applicability_domain="out_of_domain",
            explanation="Out-of-domain generated molecule prediction is low confidence.",
            warnings=[
                "Out-of-domain prediction is penalized.",
                "Generated molecule prediction is not evidence.",
            ],
            metadata=_prediction_metadata(generated=True),
            **common,
        ),
    ]


def _prediction_metadata(*, generated: bool = False) -> dict[str, Any]:
    return {
        "not_experimental_evidence": True,
        "not_assay_result": True,
        "not_evidence_item": True,
        "display_calibration_status": "calibrated",
        "labeled_from_seed_result": False,
        "generated_requires_exact_result_linkage": generated,
    }


def _training_row(feature_row: Mapping[str, Any], result: AssayResult) -> dict[str, Any]:
    return {
        **dict(feature_row),
        "canonical_smiles": result.canonical_smiles,
        "inchi_key": result.inchi_key,
        "source_result_id": result.result_id,
        "result_date": result.imported_at.date().isoformat(),
        "features": {
            "has_structure": float(bool(result.canonical_smiles)),
            "result_confidence": float(result.confidence),
            "target_context_match": float(feature_row.get("target_context_match") or 0.0),
            "disease_context_match": float(feature_row.get("disease_context_match") or 0.0),
        },
    }


def _candidate_payload(result: AssayResult) -> dict[str, Any]:
    return {
        "candidate_id": result.candidate_id,
        "candidate_name": result.candidate_name,
        "candidate_origin": result.candidate_origin,
        "canonical_smiles": result.canonical_smiles,
        "inchi_key": result.inchi_key,
    }


def _generated_candidate_payload(generated_id: str) -> dict[str, Any]:
    smiles = "CCOc1ccccc1C" if generated_id == "GEN-NEAR" else "CCCCCCCCCCCCCCCC"
    return {
        "generated_id": generated_id,
        "candidate_id": generated_id,
        "candidate_name": generated_id,
        "candidate_origin": "generated",
        "canonical_smiles": smiles,
        "inchi_key": f"SYN-{generated_id}",
    }


def _generated_molecule(generated_id: str, *, metadata: Mapping[str, Any]) -> GeneratedMolecule:
    return GeneratedMolecule(
        generated_id=generated_id,
        smiles="CCOc1ccccc1C",
        canonical_smiles="CCOc1ccccc1C",
        inchi_key="SYN-GEN-NEAR",
        generation_method="synthetic_validation_fixture",
        parent_seed_ids=["CAND-1"],
        conditioned_targets=["MAOB"],
        objective_id="model-validation-objective",
        generation_round=1,
        descriptors={"heavy_atom_count": 12.0, "rotatable_bonds": 3.0},
        validation=ChemicalValidationResult(
            valid_rdkit_mol=True,
            sanitization_ok=True,
            canonicalization_ok=True,
            allowed_elements_ok=True,
            descriptor_bounds_ok=True,
        ),
        novelty=NoveltyAssessment(
            duplicate_of_existing=False,
            duplicate_of_generated=False,
            max_similarity_to_existing=0.72,
            nearest_existing_name="Synthetic Candidate 1",
            max_similarity_to_seed=0.72,
            nearest_seed_name="Synthetic seed 1",
            novelty_class="close_analog",
        ),
        diversity_cluster="validation-cluster",
        generation_score=0.65,
        warnings=["Generated molecule is a computational hypothesis."],
        metadata=dict(metadata),
    )


def _oracle_prediction_payload(prediction: ModelPrediction) -> dict[str, Any]:
    payload = prediction.model_dump(mode="json")
    payload["not_evidence"] = True
    payload["not_assay_result"] = True
    return payload


def _json_payloads(root: Path) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    for path in root.rglob("*.json"):
        try:
            payloads[str(path.relative_to(root))] = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
    return payloads


def _load_model(
    payloads: Mapping[str, Any],
    name: str,
    model_type: type[Any],
) -> Any | None:
    payload = payloads.get(name)
    if not isinstance(payload, Mapping):
        return None
    return model_type.model_validate(payload)


def _load_predictions(payloads: Mapping[str, Any]) -> list[ModelPrediction]:
    payload = payloads.get("model_predictions.json")
    if not isinstance(payload, Mapping):
        return []
    predictions = payload.get("predictions")
    if not isinstance(predictions, list):
        return []
    return [
        ModelPrediction.model_validate(item)
        for item in predictions
        if isinstance(item, Mapping)
    ]


def _report_texts(root: Path) -> dict[str, str]:
    texts: dict[str, str] = {}
    for path in root.rglob("*.md"):
        if path.name in {"model_guardrail_audit.md", "model_validation_report.md"}:
            continue
        texts[str(path.relative_to(root))] = path.read_text()
    return texts


def _finding(
    category: str,
    check_id: str,
    message: str,
    artifact_path: str,
    excerpt: str = "",
) -> ModelGuardrailFinding:
    return ModelGuardrailFinding(
        category=category,
        check_id=check_id,
        severity="error",
        artifact_path=artifact_path,
        message=message,
        excerpt=excerpt,
    )


__all__ = [
    "MODEL_VALIDATION_STEPS",
    "ModelGuardrailAuditReport",
    "ModelGuardrailFinding",
    "ModelValidationReport",
    "run_model_guardrail_audit",
    "run_model_validation",
]
