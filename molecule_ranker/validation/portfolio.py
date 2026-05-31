from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.portfolio import (
    Portfolio,
    PortfolioCandidate,
    PortfolioConstraint,
    PortfolioOptimizer,
    Program,
    ResourceBudget,
    build_portfolio_batch,
    compare_decision_scenarios,
    default_objectives,
    default_scenarios,
    generate_program_decision_memo,
    write_portfolio_report,
)
from molecule_ranker.portfolio.reports import validate_memo_guardrails
from molecule_ranker.portfolio.stage_gates import build_stage_gate
from molecule_ranker.validation.reports import write_json_artifact, write_markdown_artifact

PortfolioValidationStatus = Literal["pass", "fail"]
PortfolioValidationFixture = Literal[
    "golden",
    "fake_evidence",
    "generated_without_approval",
    "protocol_text",
]

PORTFOLIO_VALIDATION_STEPS = [
    "synthetic portfolio candidates built",
    "greedy optimization completed",
    "scenario analysis completed",
    "expert review batch built",
    "stage-gate decision recorded",
    "program decision memo generated",
    "portfolio artifacts and guardrails validated",
]

PORTFOLIO_GUARDRAIL_CATEGORIES = (
    "Deterministic optimizer authority",
    "Evidence and assay separation",
    "Generated candidate integrity",
    "Claim safety",
    "Protocol boundary",
    "Stage-gate approval",
)

_IGNORED_FILENAMES = {
    "portfolio_guardrail_audit.json",
    "portfolio_guardrail_audit.md",
    "portfolio_validation_report.json",
    "portfolio_validation_report.md",
}

_REVIEW_APPROVED_STATUSES = {
    "approved",
    "expert_approved",
    "reviewed",
    "triaged",
    "ready",
}

_POSITIVE_CLAIM_PATTERN = re.compile(
    r"\b(?:selected|these|the)\s+"
    r"(?:candidate|candidates|molecule|molecules|compound|compounds)\s+"
    r"(?:is|are|was|were)\s+"
    r"(?:safe|active|effective)\b",
    re.IGNORECASE,
)

_PROTOCOL_OR_CARE_PATTERN = re.compile(
    r"\b("
    r"incubate|incubation|pipette|wash|centrifuge|reagent|reagents|"
    r"final\s+concentration|assay\s+concentration|temperature|step-by-step|"
    r"synthetic\s+route|administer\s+\d|patient\s+treatment\s+plan"
    r")\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PortfolioGuardrailFinding:
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
class PortfolioGuardrailAuditReport:
    status: PortfolioValidationStatus
    root_dir: Path
    artifact_count: int
    categories: tuple[str, ...]
    findings: list[PortfolioGuardrailFinding]

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
class PortfolioValidationReport:
    status: PortfolioValidationStatus
    output_dir: Path
    fixture: str
    artifacts: list[str]
    required_steps: list[str]
    guardrail_audit: PortfolioGuardrailAuditReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
            "fixture": self.fixture,
            "artifacts": self.artifacts,
            "required_steps": self.required_steps,
            "guardrail_audit": self.guardrail_audit.as_dict(),
        }


@dataclass(frozen=True)
class _ArtifactSnapshot:
    path: Path
    relative_path: str
    text: str
    json_payload: Any | None


def run_portfolio_validation(
    *,
    output_dir: str | Path = ".molecule-ranker/validation/portfolio",
    fixture: PortfolioValidationFixture = "golden",
) -> PortfolioValidationReport:
    """Run the deterministic V1.4 portfolio validation workflow."""

    resolved_output = Path(output_dir).resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    _write_portfolio_validation_workflow(resolved_output, fixture=fixture)
    audit = run_portfolio_guardrail_audit(resolved_output)
    artifacts = sorted(
        str(path.relative_to(resolved_output))
        for path in resolved_output.rglob("*")
        if path.is_file()
    )
    report = PortfolioValidationReport(
        status="pass" if audit.status == "pass" else "fail",
        output_dir=resolved_output,
        fixture=fixture,
        artifacts=artifacts,
        required_steps=PORTFOLIO_VALIDATION_STEPS,
        guardrail_audit=audit,
    )
    write_json_artifact(resolved_output / "portfolio_validation_report.json", report.as_dict())
    write_markdown_artifact(
        resolved_output / "portfolio_validation_report.md",
        "V1.4 Portfolio Validation Report",
        [
            f"- Status: `{report.status}`",
            f"- Fixture: `{fixture}`",
            f"- Guardrail findings: {len(audit.findings)}",
            "",
            "## Required Steps",
            *[f"- {step}" for step in report.required_steps],
        ],
    )
    return report


def run_portfolio_guardrail_audit(path: str | Path) -> PortfolioGuardrailAuditReport:
    root = Path(path).resolve()
    artifacts = _load_artifacts(root)
    findings: list[PortfolioGuardrailFinding] = []

    candidates = _portfolio_candidates(artifacts)
    optimization = _json_by_name(artifacts, "portfolio_optimization.json")
    selected_ids = _selected_candidate_ids(optimization)
    candidate_index = {candidate.portfolio_candidate_id: candidate for candidate in candidates}

    findings.extend(_optimizer_authority_findings(artifacts, optimization))
    findings.extend(_evidence_assay_separation_findings(artifacts))
    findings.extend(
        _generated_candidate_integrity_findings(candidates, selected_ids, candidate_index)
    )
    findings.extend(_generated_review_policy_findings(optimization, selected_ids, candidate_index))
    findings.extend(_claim_and_protocol_findings(artifacts))
    findings.extend(_stage_gate_approval_findings(artifacts))

    report = PortfolioGuardrailAuditReport(
        status="fail" if findings else "pass",
        root_dir=root,
        artifact_count=len(artifacts),
        categories=PORTFOLIO_GUARDRAIL_CATEGORIES,
        findings=_dedupe_findings(findings),
    )
    _write_portfolio_guardrail_audit_reports(report)
    return report


def _write_portfolio_validation_workflow(
    output_dir: Path,
    *,
    fixture: PortfolioValidationFixture,
) -> None:
    candidates = _synthetic_portfolio_candidates()
    portfolio = _synthetic_portfolio(candidates)
    optimization = PortfolioOptimizer(algorithm="greedy", random_seed=11).optimize(portfolio)
    selection = optimization.selections[0]
    scenario_analysis = compare_decision_scenarios(
        portfolio,
        [
            scenario
            for scenario in default_scenarios()
            if scenario.scenario_id in {"conservative", "exploration", "safety_first"}
        ],
        algorithm="greedy",
        random_seed=11,
    )
    batch = build_portfolio_batch(
        candidates,
        batch_type="expert_review_batch",
        selection=selection,
        max_candidates=3,
    )
    stage_candidate = candidates[0].model_copy(
        update={
            "metadata": {
                **candidates[0].metadata,
                "portfolio_selection_status": "selected",
            }
        },
        deep=True,
    )
    stage_gate = build_stage_gate(
        stage_candidate,
        from_stage="computational_triage",
        to_stage="assay_candidate",
        require_human_approval=True,
    )
    stage_gate = stage_gate.model_copy(
        update={
            "metadata": {
                **stage_gate.metadata,
                "policy_require_human_approval": True,
            }
        },
        deep=True,
    )
    memo = generate_program_decision_memo(
        optimization,
        selection,
        scenario_analysis=scenario_analysis,
        candidate_summaries=[candidate.model_dump(mode="json") for candidate in candidates],
        output_dir=output_dir,
    )
    write_portfolio_report(
        optimization,
        output_dir,
        selection=selection,
        candidates=candidates,
        scenario_analysis=scenario_analysis,
        stage_gates=[stage_gate],
        batches=[batch],
    )
    write_json_artifact(
        output_dir / "portfolio_candidates.json",
        {
            "artifact_type": "portfolio_candidates",
            "version": "1.4.0",
            "portfolio_candidates": [candidate.model_dump(mode="json") for candidate in candidates],
        },
    )
    write_json_artifact(
        output_dir / "portfolio_optimization.json",
        {
            **optimization.model_dump(mode="json"),
            "metadata": {
                **optimization.metadata,
                "codex_generated_selection": False,
                "deterministic_optimizer_output": True,
            },
        },
    )
    write_json_artifact(
        output_dir / "scenario_analysis.json",
        scenario_analysis.model_dump(mode="json"),
    )
    write_json_artifact(output_dir / "portfolio_batch.json", batch.model_dump(mode="json"))
    write_json_artifact(
        output_dir / "stage_gate_decisions.json",
        {
            "stage_gates": [stage_gate.model_dump(mode="json")],
            "policy": {"require_human_approval": True},
        },
    )
    write_json_artifact(
        output_dir / "program_decision_memo.json",
        memo.model_dump(mode="json"),
    )
    _apply_fixture_mutation(output_dir, fixture=fixture, optimization=optimization)


def _synthetic_portfolio_candidates() -> list[PortfolioCandidate]:
    return [
        PortfolioCandidate(
            portfolio_candidate_id="portfolio-existing-a",
            source_candidate_id="existing-a",
            candidate_name="Existing A",
            origin="existing",
            canonical_smiles="CCO",
            inchi_key="VALIDATIONEXISTINGA",
            disease_name="Synthetic validation disease",
            target_symbols=["TGT1"],
            mechanism_label="source-backed mechanism A",
            chemical_series_id="series-a",
            scaffold_id="scaffold-a",
            evidence_score=0.86,
            developability_score=0.78,
            experimental_support_score=0.74,
            predictive_model_score=0.66,
            structure_score=0.72,
            experiment_readiness_score=0.84,
            uncertainty_score=0.22,
            novelty_score=0.31,
            diversity_features={"evidence_sources": ["synthetic_validation_fixture"]},
            risk_flags=[],
            blocking_risks=[],
            review_status="approved",
            direct_experimental_evidence=True,
            metadata={
                "model_prediction_calibrated": True,
                "portfolio_selection_status": "eligible",
                "artifact_refs": ["synthetic-candidates:v1"],
            },
        ),
        PortfolioCandidate(
            portfolio_candidate_id="portfolio-generated-a",
            source_candidate_id="generated-a",
            candidate_name="Generated A",
            origin="generated",
            canonical_smiles="CCN",
            inchi_key="VALIDATIONGENERATEDA",
            disease_name="Synthetic validation disease",
            target_symbols=["TGT2"],
            mechanism_label="source-backed mechanism B",
            chemical_series_id="series-b",
            scaffold_id="scaffold-b",
            evidence_score=None,
            generation_score=0.91,
            developability_score=0.72,
            experimental_support_score=None,
            predictive_model_score=0.64,
            structure_score=0.68,
            experiment_readiness_score=0.76,
            uncertainty_score=0.71,
            novelty_score=0.86,
            diversity_features={
                "evidence_sources": [],
                "active_learning_priority": "review_priority",
            },
            risk_flags=["model_domain_uncertainty"],
            blocking_risks=[],
            review_status="expert_approved",
            direct_experimental_evidence=False,
            metadata={
                "model_prediction_calibrated": True,
                "portfolio_selection_status": "eligible",
                "artifact_refs": ["synthetic-generation:v1"],
            },
        ),
        PortfolioCandidate(
            portfolio_candidate_id="portfolio-existing-b",
            source_candidate_id="existing-b",
            candidate_name="Existing B",
            origin="existing",
            canonical_smiles="CCC",
            disease_name="Synthetic validation disease",
            target_symbols=["TGT3"],
            mechanism_label="source-backed mechanism C",
            chemical_series_id="series-c",
            scaffold_id="scaffold-c",
            evidence_score=0.58,
            developability_score=0.69,
            experimental_support_score=0.45,
            predictive_model_score=0.55,
            structure_score=0.51,
            experiment_readiness_score=0.63,
            uncertainty_score=0.49,
            novelty_score=0.42,
            diversity_features={"evidence_sources": ["synthetic_validation_fixture"]},
            risk_flags=[],
            blocking_risks=[],
            review_status="reviewed",
            direct_experimental_evidence=True,
            metadata={
                "model_prediction_calibrated": True,
                "portfolio_selection_status": "eligible",
                "artifact_refs": ["synthetic-candidates:v1"],
            },
        ),
        PortfolioCandidate(
            portfolio_candidate_id="portfolio-critical-risk",
            source_candidate_id="existing-risk",
            candidate_name="Critical Risk Candidate",
            origin="existing",
            canonical_smiles="CCCl",
            disease_name="Synthetic validation disease",
            target_symbols=["TGT2"],
            chemical_series_id="series-risk",
            scaffold_id="scaffold-risk",
            evidence_score=0.77,
            developability_score=0.12,
            experimental_support_score=0.4,
            predictive_model_score=0.5,
            structure_score=0.48,
            experiment_readiness_score=0.2,
            uncertainty_score=0.66,
            novelty_score=0.38,
            risk_flags=["critical_developability_risk"],
            blocking_risks=["critical_developability_risk"],
            review_status="hold",
            direct_experimental_evidence=True,
            metadata={
                "model_prediction_calibrated": True,
                "portfolio_selection_status": "blocked",
                "artifact_refs": ["synthetic-developability:v1"],
            },
        ),
    ]


def _synthetic_portfolio(candidates: Sequence[PortfolioCandidate]) -> Portfolio:
    constraints = [
        PortfolioConstraint(
            constraint_id="validation-max-candidates",
            name="Validation max candidates",
            constraint_type="max_candidates",
            value=2,
            hard=True,
            violation_action="reject",
            description="Limit the synthetic validation portfolio size.",
        ),
        PortfolioConstraint(
            constraint_id="validation-max-generated-fraction",
            name="Validation generated fraction",
            constraint_type="max_generated_fraction",
            value=0.5,
            hard=True,
            violation_action="reject",
            description="Limit generated-only concentration in the validation portfolio.",
        ),
        PortfolioConstraint(
            constraint_id="validation-generated-review",
            name="Validation generated review approval",
            constraint_type="require_review_approval_for_generated",
            value=True,
            hard=True,
            violation_action="reject",
            description="Require review approval for generated hypotheses.",
        ),
        PortfolioConstraint(
            constraint_id="validation-exclude-critical-risk",
            name="Validation critical risk exclusion",
            constraint_type="exclude_critical_developability_risk",
            value=True,
            hard=True,
            violation_action="reject",
            description="Exclude critical developability risk annotations.",
        ),
    ]
    return Portfolio(
        portfolio_id="validation-portfolio-v14",
        program=Program(
            program_id="validation-program-v14",
            name="V1.4 validation program",
            disease_focus=["Synthetic validation disease"],
            target_focus=["TGT1", "TGT2", "TGT3"],
            description="Synthetic deterministic portfolio validation fixture.",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            metadata={"source": "synthetic_validation_fixture"},
        ),
        candidates=list(candidates),
        objectives=default_objectives(),
        constraints=constraints,
        budget=ResourceBudget(
            budget_id="validation-budget",
            name="Validation budget",
            max_candidates=2,
            max_generated_candidates=1,
            max_assay_slots=2,
            max_review_hours=4.0,
        ),
        metadata={
            "algorithm": "greedy",
            "random_seed": 11,
            "deterministic_validation": True,
            "codex_generated_selection": False,
        },
    )


def _apply_fixture_mutation(
    output_dir: Path,
    *,
    fixture: PortfolioValidationFixture,
    optimization: Any,
) -> None:
    if fixture == "golden":
        return
    if fixture == "fake_evidence":
        write_json_artifact(
            output_dir / "fake_portfolio_evidence.json",
            {
                "artifact_type": "biomedical_evidence",
                "source_system": "portfolio_optimization",
                "portfolio_result_is_evidence": True,
                "optimization_run_id": optimization.optimization_run_id,
            },
        )
    elif fixture == "generated_without_approval":
        payload = json.loads((output_dir / "portfolio_candidates.json").read_text())
        for candidate in payload["portfolio_candidates"]:
            if candidate["portfolio_candidate_id"] == "portfolio-generated-a":
                candidate["review_status"] = "pending"
        (output_dir / "portfolio_candidates.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n"
        )
        optimization_payload = json.loads((output_dir / "portfolio_optimization.json").read_text())
        optimization_payload["selections"][0]["selected_candidate_ids"] = [
            "portfolio-existing-a",
            "portfolio-generated-a",
        ]
        (output_dir / "portfolio_optimization.json").write_text(
            json.dumps(optimization_payload, indent=2, sort_keys=True) + "\n"
        )
    elif fixture == "protocol_text":
        (output_dir / "portfolio_batch.json").write_text(
            json.dumps(
                {
                    "batch_id": "invalid-protocol-batch",
                    "batch_type": "assay_triage_batch",
                    "candidate_ids": ["portfolio-existing-a"],
                    "purpose": "Use assay protocol text with reagent and incubation details.",
                    "high_level_followup_categories": ["target-engagement category"],
                    "rationale": "Invalid fixture for guardrail validation.",
                    "required_approvals": [],
                    "warnings": [],
                    "metadata": {},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )


def _load_artifacts(root: Path) -> list[_ArtifactSnapshot]:
    artifacts: list[_ArtifactSnapshot] = []
    if not root.exists():
        return artifacts
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        if path.name in _IGNORED_FILENAMES:
            continue
        text = path.read_text(errors="ignore")
        payload: Any | None = None
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
        artifacts.append(
            _ArtifactSnapshot(
                path=path,
                relative_path=str(path.relative_to(root)),
                text=text,
                json_payload=payload,
            )
        )
    return artifacts


def _json_by_name(artifacts: Sequence[_ArtifactSnapshot], name: str) -> Any | None:
    for artifact in artifacts:
        if artifact.path.name == name:
            return artifact.json_payload
    return None


def _portfolio_candidates(artifacts: Sequence[_ArtifactSnapshot]) -> list[PortfolioCandidate]:
    payload = _json_by_name(artifacts, "portfolio_candidates.json")
    if not isinstance(payload, Mapping):
        return []
    raw_candidates = payload.get("portfolio_candidates")
    if not isinstance(raw_candidates, list):
        return []
    candidates: list[PortfolioCandidate] = []
    for raw in raw_candidates:
        if isinstance(raw, Mapping):
            try:
                candidates.append(PortfolioCandidate.model_validate(raw))
            except ValueError:
                continue
    return candidates


def _selected_candidate_ids(optimization: Any | None) -> list[str]:
    if not isinstance(optimization, Mapping):
        return []
    selections = optimization.get("selections")
    if not isinstance(selections, list) or not selections:
        return []
    selected = (
        selections[0].get("selected_candidate_ids")
        if isinstance(selections[0], Mapping)
        else None
    )
    if not isinstance(selected, list):
        return []
    return [str(candidate_id) for candidate_id in selected]


def _optimizer_authority_findings(
    artifacts: Sequence[_ArtifactSnapshot],
    optimization: Any | None,
) -> list[PortfolioGuardrailFinding]:
    findings: list[PortfolioGuardrailFinding] = []
    if not isinstance(optimization, Mapping):
        findings.append(
            _finding(
                "Deterministic optimizer authority",
                "optimizer_output_required",
                "portfolio_optimization.json",
                "Portfolio selections require deterministic optimizer output.",
            )
        )
    else:
        raw_metadata = optimization.get("metadata")
        metadata: Mapping[str, Any] = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        if metadata.get("codex_generated_selection") is True:
            findings.append(
                _finding(
                    "Deterministic optimizer authority",
                    "codex_cannot_select_portfolio",
                    "portfolio_optimization.json",
                    "Codex output is marked as the portfolio selection authority.",
                )
            )
        if (
            metadata.get("deterministic_optimizer_output") is not True
            and metadata.get("deterministic_selection") is not True
        ):
            findings.append(
                _finding(
                    "Deterministic optimizer authority",
                    "deterministic_optimizer_output_required",
                    "portfolio_optimization.json",
                    "Optimization output must be deterministically validated.",
                )
            )
    for artifact in artifacts:
        if artifact.json_payload is None:
            continue
        for path, value in _walk_json(artifact.json_payload):
            key = str(path[-1]).lower() if path else ""
            if key in {"codex_selected_candidate_ids", "codex_portfolio_selection"} and value:
                findings.append(
                    _finding(
                        "Deterministic optimizer authority",
                        "codex_cannot_select_portfolio",
                        artifact.relative_path,
                        "Codex-selected portfolio candidates are not allowed without "
                        "optimizer authority.",
                        excerpt=f"{'.'.join(path)}={value}",
                    )
                )
    return findings


def _evidence_assay_separation_findings(
    artifacts: Sequence[_ArtifactSnapshot],
) -> list[PortfolioGuardrailFinding]:
    findings: list[PortfolioGuardrailFinding] = []
    for artifact in artifacts:
        if artifact.json_payload is None:
            continue
        for path, value in _walk_json(artifact.json_payload):
            key = str(path[-1]).lower() if path else ""
            if key in {"portfolio_result_is_evidence", "creates_evidence_items"} and value is True:
                findings.append(
                    _finding(
                        "Evidence and assay separation",
                        "portfolio_result_not_biomedical_evidence",
                        artifact.relative_path,
                        "Portfolio outputs must not become biomedical evidence artifacts.",
                        excerpt=f"{'.'.join(path)}=true",
                    )
                )
            if key == "artifact_type" and str(value).lower() in {
                "biomedical_evidence",
                "evidence_item",
            }:
                source = _sibling_value(artifact.json_payload, path, "source_system")
                if source == "portfolio_optimization":
                    findings.append(
                        _finding(
                            "Evidence and assay separation",
                            "portfolio_result_not_biomedical_evidence",
                            artifact.relative_path,
                            "Portfolio output is labeled as biomedical evidence.",
                            excerpt=f"{'.'.join(path)}={value}",
                        )
                    )
            if (
                key in {"portfolio_result_is_assay_result", "creates_assay_results"}
                and value is True
            ):
                findings.append(
                    _finding(
                        "Evidence and assay separation",
                        "portfolio_result_not_assay_result",
                        artifact.relative_path,
                        "Portfolio outputs must not become assay result artifacts.",
                        excerpt=f"{'.'.join(path)}=true",
                    )
                )
            if key == "artifact_type" and str(value).lower() == "assay_result":
                source = _sibling_value(artifact.json_payload, path, "source_system")
                if source == "portfolio_optimization":
                    findings.append(
                        _finding(
                            "Evidence and assay separation",
                            "portfolio_result_not_assay_result",
                            artifact.relative_path,
                            "Portfolio output is labeled as an assay result.",
                            excerpt=f"{'.'.join(path)}={value}",
                        )
                    )
    return findings


def _generated_candidate_integrity_findings(
    candidates: Sequence[PortfolioCandidate],
    selected_ids: Sequence[str],
    candidate_index: Mapping[str, PortfolioCandidate],
) -> list[PortfolioGuardrailFinding]:
    findings: list[PortfolioGuardrailFinding] = []
    for candidate in candidates:
        if candidate.origin != "generated":
            continue
        if (
            not candidate.direct_experimental_evidence
            and not candidate.generated_without_direct_evidence
        ):
            findings.append(
                _finding(
                    "Generated candidate integrity",
                    "generated_label_preserved",
                    "portfolio_candidates.json",
                    "Generated candidates without exact linked evidence must retain "
                    "generated-only labeling.",
                    excerpt=candidate.portfolio_candidate_id,
                )
            )
    for candidate_id in selected_ids:
        candidate = candidate_index.get(candidate_id)
        if candidate_id.startswith("portfolio-generated") and candidate is None:
            findings.append(
                _finding(
                    "Generated candidate integrity",
                    "selected_generated_candidate_must_have_origin",
                    "portfolio_optimization.json",
                    "Selected generated candidate is missing from candidate artifact.",
                    excerpt=candidate_id,
                )
            )
    return findings


def _generated_review_policy_findings(
    optimization: Any | None,
    selected_ids: Sequence[str],
    candidate_index: Mapping[str, PortfolioCandidate],
) -> list[PortfolioGuardrailFinding]:
    if not _requires_generated_review(optimization):
        return []
    findings: list[PortfolioGuardrailFinding] = []
    for candidate_id in selected_ids:
        candidate = candidate_index.get(candidate_id)
        if candidate is None or candidate.origin != "generated":
            continue
        if not _review_approved(candidate.review_status):
            findings.append(
                _finding(
                    "Generated candidate integrity",
                    "generated_high_priority_requires_approval",
                    "portfolio_optimization.json",
                    "Selected generated candidates require review approval under policy.",
                    excerpt=candidate_id,
                )
            )
    return findings


def _claim_and_protocol_findings(
    artifacts: Sequence[_ArtifactSnapshot],
) -> list[PortfolioGuardrailFinding]:
    findings: list[PortfolioGuardrailFinding] = []
    for artifact in artifacts:
        for violation in validate_memo_guardrails(artifact.text):
            if violation in {
                "bench-instruction phrase",
                "chemistry-execution phrase",
                "care-plan detail",
                "treatment phrase",
            }:
                continue
            findings.append(
                _finding(
                    "Claim safety",
                    "no_selected_molecule_positive_claim",
                    artifact.relative_path,
                    "Portfolio text must not call selected molecules safe, active, or effective.",
                    excerpt=violation,
                )
            )
        positive_claim = _POSITIVE_CLAIM_PATTERN.search(artifact.text)
        if positive_claim:
            findings.append(
                _finding(
                    "Claim safety",
                    "no_selected_molecule_positive_claim",
                    artifact.relative_path,
                    "Portfolio text must not call selected molecules safe, active, or effective.",
                    excerpt=positive_claim.group(0),
                )
            )
        protocol_match = _PROTOCOL_OR_CARE_PATTERN.search(artifact.text)
        if protocol_match:
            findings.append(
                _finding(
                    "Protocol boundary",
                    "no_protocol_synthesis_or_care_details",
                    artifact.relative_path,
                    "Portfolio artifacts must not contain operational protocol, "
                    "chemistry execution, or care-plan details.",
                    excerpt=protocol_match.group(0),
                )
            )
    return findings


def _stage_gate_approval_findings(
    artifacts: Sequence[_ArtifactSnapshot],
) -> list[PortfolioGuardrailFinding]:
    payload = _json_by_name(artifacts, "stage_gate_decisions.json")
    if not isinstance(payload, Mapping):
        return []
    raw_policy = payload.get("policy")
    policy: Mapping[str, Any] = raw_policy if isinstance(raw_policy, Mapping) else {}
    policy_requires_approval = policy.get("require_human_approval") is True
    gates = payload.get("stage_gates")
    if not isinstance(gates, list):
        return []
    findings: list[PortfolioGuardrailFinding] = []
    for gate in gates:
        if not isinstance(gate, Mapping):
            continue
        raw_metadata = gate.get("metadata")
        metadata: Mapping[str, Any] = raw_metadata if isinstance(raw_metadata, Mapping) else {}
        requires_approval = (
            policy_requires_approval
            or metadata.get("policy_require_human_approval") is True
            or bool(gate.get("required_approvals"))
        )
        if not requires_approval:
            continue
        approved_by = metadata.get("approved_by")
        decision = gate.get("decision")
        if decision == "advance" and not approved_by:
            findings.append(
                _finding(
                    "Stage-gate approval",
                    "stage_gate_human_approval_required",
                    "stage_gate_decisions.json",
                    "Stage gate advanced without recorded human approval.",
                    excerpt=str(gate.get("stage_gate_id") or ""),
                )
            )
        if policy_requires_approval and not gate.get("required_approvals"):
            findings.append(
                _finding(
                    "Stage-gate approval",
                    "stage_gate_human_approval_recorded",
                    "stage_gate_decisions.json",
                    "Configured human-approval policy must be represented in stage-gate approvals.",
                    excerpt=str(gate.get("stage_gate_id") or ""),
                )
            )
    return findings


def _requires_generated_review(optimization: Any | None) -> bool:
    if not isinstance(optimization, Mapping):
        return True
    constraints = optimization.get("constraints")
    if not isinstance(constraints, list):
        return True
    for constraint in constraints:
        if not isinstance(constraint, Mapping):
            continue
        if (
            constraint.get("constraint_type") == "require_review_approval_for_generated"
            and constraint.get("value") is True
        ):
            return True
    return False


def _review_approved(status: str | None) -> bool:
    if status is None:
        return False
    normalized = status.lower().replace("-", "_").replace(" ", "_")
    return normalized in _REVIEW_APPROVED_STATUSES


def _walk_json(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], Any]]:
    items: list[tuple[tuple[str, ...], Any]] = [(path, value)]
    if isinstance(value, Mapping):
        for key, child in value.items():
            items.extend(_walk_json(child, (*path, str(key))))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            items.extend(_walk_json(child, (*path, str(index))))
    return items


def _sibling_value(root: Any, path: tuple[str, ...], key: str) -> Any:
    parent = root
    for part in path[:-1]:
        if isinstance(parent, Mapping):
            parent = parent.get(part)
        elif isinstance(parent, list) and part.isdigit():
            index = int(part)
            parent = parent[index] if index < len(parent) else None
        else:
            return None
    if isinstance(parent, Mapping):
        return parent.get(key)
    return None


def _finding(
    category: str,
    check_id: str,
    artifact_path: str,
    message: str,
    *,
    severity: str = "error",
    excerpt: str = "",
) -> PortfolioGuardrailFinding:
    return PortfolioGuardrailFinding(
        category=category,
        check_id=check_id,
        severity=severity,
        artifact_path=artifact_path,
        message=message,
        excerpt=excerpt[:240],
    )


def _dedupe_findings(
    findings: Sequence[PortfolioGuardrailFinding],
) -> list[PortfolioGuardrailFinding]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[PortfolioGuardrailFinding] = []
    for finding in findings:
        key = (finding.category, finding.check_id, finding.artifact_path, finding.excerpt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _write_portfolio_guardrail_audit_reports(report: PortfolioGuardrailAuditReport) -> None:
    write_json_artifact(report.root_dir / "portfolio_guardrail_audit.json", report.as_dict())
    lines = [
        f"- Status: `{report.status}`",
        f"- Artifacts audited: {report.artifact_count}",
        f"- Findings: {len(report.findings)}",
        "",
        "## Findings",
    ]
    if report.findings:
        lines.extend(
            [
                (
                    f"- `{finding.check_id}` in `{finding.artifact_path}`: "
                    f"{finding.message}"
                )
                for finding in report.findings
            ]
        )
    else:
        lines.append("- None.")
    write_markdown_artifact(
        report.root_dir / "portfolio_guardrail_audit.md",
        "V1.4 Portfolio Guardrail Audit",
        lines,
    )
