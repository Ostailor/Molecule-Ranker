from __future__ import annotations

import json
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.validation.reports import write_json_artifact, write_markdown_artifact

DesignValidationStatus = Literal["pass", "fail"]

DESIGN_AUDIT_CATEGORIES = (
    "Target and artifact grounding",
    "Evidence integrity",
    "Generated molecule integrity",
    "Experimental result integrity",
    "Codex plan integrity",
    "Export integrity",
    "Medical/synthesis/lab-protocol safety",
)

IGNORED_FILENAMES = {
    "design_guardrail_audit.json",
    "design_guardrail_audit.md",
    "design_validation_report.json",
    "design_validation_report.md",
}


@dataclass(frozen=True)
class DesignGuardrailFinding:
    category: str
    check_id: str
    severity: str
    artifact_path: str
    message: str
    excerpt: str

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
class DesignGuardrailAuditReport:
    status: DesignValidationStatus
    root_dir: Path
    artifact_count: int
    categories: tuple[str, ...]
    findings: list[DesignGuardrailFinding]

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
class DesignValidationReport:
    status: DesignValidationStatus
    output_dir: Path
    artifacts: list[str]
    required_steps: list[str]
    guardrail_audit: DesignGuardrailAuditReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_dir": str(self.output_dir),
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

    @property
    def is_design(self) -> bool:
        lowered = self.relative_path.lower()
        return any(
            marker in lowered
            for marker in (
                "design",
                "generated",
                "oracle",
                "uncertainty",
                "readiness",
                "scaffold",
            )
        )

    @property
    def is_generated(self) -> bool:
        lowered = self.relative_path.lower()
        return "generated" in lowered or "generation" in lowered

    @property
    def is_codex_plan(self) -> bool:
        if "codex" in self.relative_path.lower():
            return True
        payload = self.json_payload
        return isinstance(payload, dict) and bool(payload.get("codex_task_result_id"))


def run_design_validation(
    *,
    output_dir: str | Path = ".molecule-ranker/validation/design",
    input_artifact_dir: str | Path | None = None,
) -> DesignValidationReport:
    """Run the deterministic V1.1 design optimization golden workflow."""

    resolved_output = Path(output_dir).resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    _write_design_workflow_artifacts(
        resolved_output,
        input_artifact_dir=Path(input_artifact_dir).resolve() if input_artifact_dir else None,
    )
    audit = run_design_guardrail_audit(resolved_output)
    artifacts = [
        "candidates.json",
        "design_plan.json",
        "seed_scaffold_selection.json",
        "generated_candidates_v2.json",
        "oracle_scores.json",
        "uncertainty.json",
        "experiment_readiness.json",
        "generated_report.md",
        "design_guardrail_audit.json",
        "design_guardrail_audit.md",
    ]
    report = DesignValidationReport(
        status="pass" if audit.status == "pass" else "fail",
        output_dir=resolved_output,
        artifacts=artifacts,
        required_steps=[
            "existing run artifacts loaded",
            "design plan built",
            "seeds and scaffolds selected",
            "generator ensemble executed",
            "oracle scores computed",
            "uncertainty computed",
            "experiment readiness computed",
            "report produced",
            "design guardrails verified",
        ],
        guardrail_audit=audit,
    )
    write_json_artifact(resolved_output / "design_validation_report.json", report.as_dict())
    write_markdown_artifact(
        resolved_output / "design_validation_report.md",
        "V1.1 Design Validation Report",
        [
            f"- Status: `{report.status}`",
            f"- Guardrail findings: {len(audit.findings)}",
            "",
            "## Required Steps",
            *[f"- {step}" for step in report.required_steps],
        ],
    )
    return report


def run_design_guardrail_audit(path: str | Path) -> DesignGuardrailAuditReport:
    root = Path(path).resolve()
    artifacts = _load_artifacts(root)
    source_targets = _source_target_symbols(artifacts)
    imported_results = _imported_result_index(artifacts)
    findings: list[DesignGuardrailFinding] = []
    for artifact in artifacts:
        findings.extend(_text_findings(artifact))
        if artifact.json_payload is None:
            continue
        findings.extend(_target_findings(artifact, source_targets=source_targets))
        findings.extend(_evidence_findings(artifact, imported_results=imported_results))
        findings.extend(_codex_plan_findings(artifact, root=root, source_targets=source_targets))
        findings.extend(_export_findings(artifact))
    report = DesignGuardrailAuditReport(
        status="fail" if findings else "pass",
        root_dir=root,
        artifact_count=len(artifacts),
        categories=DESIGN_AUDIT_CATEGORIES,
        findings=findings,
    )
    write_design_guardrail_audit_reports(report)
    return report


def write_design_guardrail_audit_reports(report: DesignGuardrailAuditReport) -> None:
    report.root_dir.mkdir(parents=True, exist_ok=True)
    (report.root_dir / "design_guardrail_audit.json").write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n"
    )
    (report.root_dir / "design_guardrail_audit.md").write_text(
        _render_design_guardrail_markdown(report)
    )


def _write_design_workflow_artifacts(
    output_dir: Path,
    *,
    input_artifact_dir: Path | None,
) -> None:
    source_candidates = input_artifact_dir / "candidates.json" if input_artifact_dir else None
    if source_candidates and source_candidates.exists():
        shutil.copyfile(source_candidates, output_dir / "candidates.json")
    else:
        write_json_artifact(output_dir / "candidates.json", _synthetic_candidates())
    write_json_artifact(output_dir / "design_plan.json", _synthetic_design_plan())
    write_json_artifact(output_dir / "seed_scaffold_selection.json", _synthetic_seed_scaffolds())
    write_json_artifact(output_dir / "generated_candidates_v2.json", _synthetic_generated())
    write_json_artifact(output_dir / "oracle_scores.json", _synthetic_oracle_scores())
    write_json_artifact(output_dir / "uncertainty.json", _synthetic_uncertainty())
    write_json_artifact(output_dir / "experiment_readiness.json", _synthetic_readiness())
    write_markdown_artifact(
        output_dir / "generated_report.md",
        "V1.1 Generated Molecule Design Golden Report",
        [
            "Generated molecules are computational hypotheses for expert triage.",
            "Experiment-readiness means worth expert review, not experimental confirmation.",
            "Operational chemistry directions are omitted.",
            "Procedural wet-lab directions are omitted.",
            "Clinical amount guidance and safety, efficacy, or activity claims are omitted.",
        ],
    )


def _synthetic_candidates() -> dict[str, Any]:
    return {
        "success": True,
        "disease": {"canonical_name": "Synthetic Neuro Example"},
        "targets": [
            {
                "target_id": "SYN-T1",
                "symbol": "SYN1",
                "source_record_id": "synthetic-target-1",
            }
        ],
        "candidates": [
            {
                "candidate_id": "SYN-SEED-1",
                "name": "SyntheticSeedA",
                "origin": "existing",
                "known_targets": ["SYN1"],
                "chemical_metadata": {"canonical_smiles": "CCO"},
                "evidence": [
                    {
                        "source": "synthetic_validation_fixture",
                        "source_record_id": "synthetic-evidence-1",
                        "evidence_type": "molecule_target",
                        "target_symbol": "SYN1",
                    }
                ],
            }
        ],
        "summary": {"candidate_count": 1, "generated_candidate_count": 0, "target_count": 1},
    }


def _synthetic_design_plan() -> dict[str, Any]:
    return {
        "design_plan_id": "design-validation-plan",
        "disease_name": "Synthetic Neuro Example",
        "target_priorities": [{"target_symbol": "SYN1", "basis": "source artifact"}],
        "design_objectives": [
            {
                "objective_id": "objective-SYN1",
                "target_symbol": "SYN1",
                "desired_modality": "small_molecule",
                "desired_action": "unknown",
                "constraints": {"generated_hypothesis_only": True},
                "seed_ids": ["SYN-SEED-1"],
            }
        ],
        "seed_strategy": {"source": "existing run artifacts"},
        "generator_strategy": {"mode": "generator_ensemble"},
        "oracle_strategy": {"score_name": "experiment_worthiness_score"},
        "diversity_strategy": {"deduplicate": True},
        "uncertainty_strategy": {"active_learning_value": True},
        "experiment_readiness_strategy": {"human_review_required": True},
        "risks": [{"risk": "unvalidated_generated_hypothesis"}],
        "constraints": {"no_lab_protocols": True, "no_synthesis_instructions": True},
        "required_followups": [{"action": "expert medchem review"}],
        "codex_task_result_id": "deterministic-planner-disabled",
        "artifact_manifests": [{"path": "candidates.json"}],
        "metadata": {"deterministic_validation": {"approved": True}},
    }


def _synthetic_seed_scaffolds() -> dict[str, Any]:
    return {
        "seeds": [
            {
                "seed_id": "SYN-SEED-1",
                "candidate_id": "SYN-SEED-1",
                "target_symbols": ["SYN1"],
                "reason_selected": "Source-backed synthetic seed for validation.",
            }
        ],
        "scaffolds": [
            {
                "scaffold_id": "SYN-SCAF-1",
                "source_seed_ids": ["SYN-SEED-1"],
                "target_symbols": ["SYN1"],
                "reason_selected": "Deterministic scaffold validation fixture.",
            }
        ],
    }


def _synthetic_generated() -> dict[str, Any]:
    molecule = {
        "generated_id": "SYN-GEN-1",
        "origin": "generated",
        "is_generated": True,
        "label": "computational_hypothesis",
        "canonical_smiles": "CCCO",
        "conditioned_targets": ["SYN1"],
        "parent_seed_ids": ["SYN-SEED-1"],
        "evidence": [],
        "metadata": {
            "generator_name": "selfies_mutation",
            "claim_boundary": "computational triage only",
        },
    }
    return {
        "generated_count": 1,
        "retained_count": 1,
        "rejected_count": 0,
        "generated_molecules": [molecule],
        "retained_generated_molecules": [molecule],
        "rejected_generated_molecules": [],
        "warnings": [],
    }


def _synthetic_oracle_scores() -> dict[str, Any]:
    return {
        "score_name": "experiment_worthiness_score",
        "candidate_count": 1,
        "oracle_scores": [
            {
                "generated_id": "SYN-GEN-1",
                "score": 0.62,
                "confidence": 0.43,
                "explanation": "Computational triage score only.",
            }
        ],
        "claim_boundary": "not activity, binding, safety, or efficacy evidence",
    }


def _synthetic_uncertainty() -> dict[str, Any]:
    return {
        "assessments": [
            {
                "generated_id": "SYN-GEN-1",
                "applicability_domain": "near_domain",
                "evidence_uncertainty": 1.0,
                "model_uncertainty": 0.5,
            }
        ]
    }


def _synthetic_readiness() -> dict[str, Any]:
    return {
        "candidates": [
            {
                "molecule_id": "SYN-GEN-1",
                "readiness_bucket": "ready_for_expert_review",
                "top_reasons": ["source-backed seed context", "bounded uncertainty"],
                "blocking_risks": [],
                "suggested_high_level_followup": "expert medchem review",
                "warnings": ["Human review required before any experimental triage."],
            }
        ],
        "human_review_required": True,
    }


def _load_artifacts(root: Path) -> list[_ArtifactSnapshot]:
    if not root.exists() or not root.is_dir():
        return []
    artifacts: list[_ArtifactSnapshot] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in IGNORED_FILENAMES:
            continue
        if path.suffix.lower() not in {".json", ".md", ".txt", ".html", ".csv"}:
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
                relative_path=path.relative_to(root).as_posix(),
                text=text,
                json_payload=payload,
            )
        )
    return artifacts


def _text_findings(artifact: _ArtifactSnapshot) -> list[DesignGuardrailFinding]:
    rules = [
        (
            "Experimental result integrity",
            "no_fake_assay_results",
            re.compile(r"\b(?:fake|fabricated|invented)\s+assay\s+result\b", re.I),
            "Artifact contains fake or fabricated assay-result language.",
        ),
        (
            "Evidence integrity",
            "no_invented_evidence",
            re.compile(r"\b(?:fake|fabricated|invented)\s+evidence\b", re.I),
            "Artifact contains fake or fabricated evidence language.",
        ),
        (
            "Generated molecule integrity",
            "no_activity_claims",
            re.compile(
                r"\b(?:is|are|was|were)?\s*(?:active|binds?|inhibits?|activates?)\b",
                re.I,
            ),
            "Artifact contains activity, binding, inhibition, or activation claim language.",
        ),
        (
            "Generated molecule integrity",
            "no_safety_claims",
            re.compile(r"\b(?:is|are|was|were)?\s*safe\b|\bsafety\s+is\s+established\b", re.I),
            "Artifact contains safety claim language.",
        ),
        (
            "Medical/synthesis/lab-protocol safety",
            "no_synthesis_instructions",
            re.compile(r"\bsynthesis\s+(?:instructions?|route|steps?)\b", re.I),
            "Artifact contains synthesis-instruction language.",
        ),
        (
            "Medical/synthesis/lab-protocol safety",
            "no_lab_protocols",
            re.compile(r"\blab\s+protocol\b|\bwet-?lab\s+protocol\b", re.I),
            "Artifact contains lab-protocol language.",
        ),
        (
            "Medical/synthesis/lab-protocol safety",
            "no_dosing",
            re.compile(r"\bdos(?:e|ing|age)\b|\bmg/kg\b|\bmg/day\b", re.I),
            "Artifact contains dosing language.",
        ),
    ]
    findings: list[DesignGuardrailFinding] = []
    for category, check_id, pattern, message in rules:
        for match in pattern.finditer(artifact.text):
            if _is_allowed_negated_context(artifact.text, match.start()):
                continue
            findings.append(_finding(artifact, category, check_id, message, match.group(0)))
    return findings


def _target_findings(
    artifact: _ArtifactSnapshot,
    *,
    source_targets: set[str],
) -> list[DesignGuardrailFinding]:
    if artifact.json_payload is None or not artifact.is_design or not source_targets:
        return []
    findings: list[DesignGuardrailFinding] = []
    for target_symbol in _target_refs(artifact.json_payload):
        if target_symbol not in source_targets:
            findings.append(
                _structural_finding(
                    artifact,
                    "Target and artifact grounding",
                    "no_invented_targets",
                    "Design artifact references a target absent from existing run artifacts.",
                    target_symbol,
                )
            )
    return findings


def _evidence_findings(
    artifact: _ArtifactSnapshot,
    *,
    imported_results: dict[str, set[str]],
) -> list[DesignGuardrailFinding]:
    if artifact.json_payload is None:
        return []
    findings: list[DesignGuardrailFinding] = []
    for item in _dicts(artifact.json_payload):
        if item.get("evidence") and item.get("origin") == "generated":
            molecule_id = str(
                item.get("generated_id")
                or item.get("molecule_id")
                or item.get("candidate_id")
                or ""
            )
            allowed_results = imported_results.get(molecule_id, set())
            for evidence in item.get("evidence", []):
                result_ref = _result_reference(evidence)
                if not result_ref or result_ref not in allowed_results:
                    findings.append(
                        _structural_finding(
                            artifact,
                            "Generated molecule integrity",
                            "no_generated_direct_evidence_without_exact_imported_result",
                            (
                                "Generated molecule has direct evidence without an exact "
                                "imported result."
                            ),
                            result_ref or molecule_id,
                        )
                    )
        if "evidence" in item and isinstance(item.get("evidence"), list):
            for evidence in item["evidence"]:
                if isinstance(evidence, dict) and not evidence.get("source_record_id"):
                    findings.append(
                        _structural_finding(
                            artifact,
                            "Evidence integrity",
                            "no_invented_evidence",
                            "Evidence item is missing source_record_id.",
                            str(evidence.get("title") or evidence.get("source") or "evidence"),
                        )
                    )
        if item.get("source") in {"codex", "generated", "llm"} and "evidence_type" in item:
            findings.append(
                _structural_finding(
                    artifact,
                    "Evidence integrity",
                    "no_invented_evidence",
                    "Generated or Codex output is represented as evidence.",
                    str(item.get("source")),
                )
            )
        if item.get("assay_fabricated") is True or item.get("fake_assay_result") is True:
            findings.append(
                _structural_finding(
                    artifact,
                    "Experimental result integrity",
                    "no_fake_assay_results",
                    "Artifact marks an assay result as fabricated or fake.",
                    str(item.get("result_id") or "assay result"),
                )
            )
    return findings


def _codex_plan_findings(
    artifact: _ArtifactSnapshot,
    *,
    root: Path,
    source_targets: set[str],
) -> list[DesignGuardrailFinding]:
    payload = artifact.json_payload
    if not isinstance(payload, dict) or not artifact.is_codex_plan:
        return []
    findings: list[DesignGuardrailFinding] = []
    for manifest in payload.get("artifact_manifests", []):
        if not isinstance(manifest, dict):
            continue
        raw_path = manifest.get("path")
        if not raw_path:
            continue
        path = Path(str(raw_path))
        resolved = path if path.is_absolute() else root / path
        if not resolved.exists():
            findings.append(
                _structural_finding(
                    artifact,
                    "Codex plan integrity",
                    "no_codex_plan_with_unsupported_artifacts",
                    "Codex design plan references a missing artifact.",
                    str(raw_path),
                )
            )
    if source_targets:
        for target_symbol in _target_refs(payload):
            if target_symbol not in source_targets:
                findings.append(
                    _structural_finding(
                        artifact,
                        "Codex plan integrity",
                        "no_codex_plan_with_unsupported_artifacts",
                        "Codex design plan references an unsupported target.",
                        target_symbol,
                    )
                )
    return findings


def _export_findings(artifact: _ArtifactSnapshot) -> list[DesignGuardrailFinding]:
    if artifact.json_payload is None:
        return []
    lowered = artifact.relative_path.lower()
    if "export" not in lowered:
        return []
    findings: list[DesignGuardrailFinding] = []
    for item in _dicts(artifact.json_payload):
        if _is_generated_record(item) and (
            item.get("validated") is True
            or item.get("validated_active") is True
            or str(item.get("label", "")).lower() in {"validated", "validated compound"}
        ):
            findings.append(
                _structural_finding(
                    artifact,
                    "Export integrity",
                    "no_generated_molecule_exported_as_validated_compound",
                    "Generated molecule is exported as a validated compound.",
                    str(item.get("generated_id") or item.get("molecule_id") or "generated"),
                )
            )
    return findings


def _source_target_symbols(artifacts: Iterable[_ArtifactSnapshot]) -> set[str]:
    targets: set[str] = set()
    for artifact in artifacts:
        if artifact.json_payload is None:
            continue
        lowered = artifact.relative_path.lower()
        if lowered.endswith("candidates.json") or "targets" in lowered:
            targets.update(_target_refs(artifact.json_payload))
            for item in _dicts(artifact.json_payload):
                known_targets = item.get("known_targets", [])
                if not isinstance(known_targets, list):
                    continue
                for target in known_targets:
                    targets.add(str(target))
    return {target for target in targets if target}


def _target_refs(payload: Any) -> set[str]:
    refs: set[str] = set()
    for item in _dicts(payload):
        for key in ("target_symbol", "symbol"):
            value = item.get(key)
            if value:
                refs.add(str(value))
        for key in ("target_symbols", "conditioned_targets", "known_targets"):
            values = item.get(key)
            if isinstance(values, list):
                refs.update(str(value) for value in values if value)
    return refs


def _imported_result_index(artifacts: Iterable[_ArtifactSnapshot]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for artifact in artifacts:
        lowered = artifact.relative_path.lower()
        if artifact.json_payload is None or (
            "experimental" not in lowered and "assay" not in lowered
        ):
            continue
        for item in _dicts(artifact.json_payload):
            molecule_id = item.get("generated_id") or item.get("molecule_id") or item.get(
                "candidate_id"
            )
            result_ref = _result_reference(item)
            if molecule_id and result_ref and str(item.get("qc_status", "")).lower() != "failed":
                index.setdefault(str(molecule_id), set()).add(result_ref)
    return index


def _result_reference(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("result_id", "source_result_id", "experimental_result_id", "source_record_id"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _is_generated_record(item: dict[str, Any]) -> bool:
    return bool(
        item.get("origin") == "generated"
        or item.get("is_generated") is True
        or item.get("generated_id")
        or str(item.get("label", "")).lower() == "computational_hypothesis"
    )


def _dicts(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _dicts(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _dicts(item)


def _is_allowed_negated_context(text: str, start: int) -> bool:
    before = text[max(0, start - 90) : start].lower()
    return bool(
        re.search(
            r"\b(?:no|not|never|without|does\s+not|do\s+not|cannot|is\s+not|are\s+not)\b"
            r"[^.\n;:]{0,90}$",
            before,
        )
        or re.search(
            r"\b(?:omit|omits|omitted|exclude|excludes|excluded|excluding)\b"
            r"[^.\n;:]{0,90}$",
            before,
        )
    )


def _finding(
    artifact: _ArtifactSnapshot,
    category: str,
    check_id: str,
    message: str,
    excerpt: str,
) -> DesignGuardrailFinding:
    return DesignGuardrailFinding(
        category=category,
        check_id=check_id,
        severity="error",
        artifact_path=artifact.relative_path,
        message=message,
        excerpt=excerpt,
    )


def _structural_finding(
    artifact: _ArtifactSnapshot,
    category: str,
    check_id: str,
    message: str,
    excerpt: str,
) -> DesignGuardrailFinding:
    return _finding(artifact, category, check_id, message, excerpt)


def _render_design_guardrail_markdown(report: DesignGuardrailAuditReport) -> str:
    lines = [
        "# V1.1 Design Guardrail Audit",
        "",
        f"- Status: `{report.status}`",
        f"- Artifact count: {report.artifact_count}",
        f"- Finding count: {len(report.findings)}",
        "",
        "## Categories",
        "",
        *[f"- {category}" for category in report.categories],
        "",
        "## Findings",
        "",
    ]
    if not report.findings:
        lines.append("No design guardrail violations found.")
    for finding in report.findings:
        lines.extend(
            [
                f"### {finding.check_id}",
                "",
                f"- Category: {finding.category}",
                f"- Severity: {finding.severity}",
                f"- Artifact: `{finding.artifact_path}`",
                f"- Message: {finding.message}",
                f"- Excerpt: {finding.excerpt}",
                "",
            ]
        )
    return "\n".join(lines) + "\n"
