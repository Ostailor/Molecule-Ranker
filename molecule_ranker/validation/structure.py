from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from molecule_ranker.codex_backbone.guardrails import detect_structure_artifact_violations
from molecule_ranker.codex_backbone.schemas import CodexTaskResult
from molecule_ranker.validation.reports import write_json_artifact, write_markdown_artifact

StructureValidationStatus = Literal["pass", "fail"]
StructureValidationFixture = Literal[
    "golden",
    "overclaim",
    "fake_docking_score",
    "fake_binding_site_source",
]

STRUCTURE_VALIDATION_STEPS = [
    "mock target with structures",
    "structure selected",
    "receptor prepared",
    "ligands prepared",
    "binding site defined",
    "null docking run completed",
    "pose QC completed",
    "interaction profiling completed",
    "consensus rescoring completed",
    "structure report generated",
    "structure guardrails verified",
]

STRUCTURE_GUARDRAIL_CATEGORIES = (
    "Evidence separation",
    "Structure artifact grounding",
    "Binding-site provenance",
    "Claim safety",
    "Generated molecule integrity",
    "Predicted-structure labeling",
    "Codex output grounding",
)

IGNORED_FILENAMES = {
    "structure_guardrail_audit.json",
    "structure_guardrail_audit.md",
    "structure_validation_report.json",
    "structure_validation_report.md",
}

_STRUCTURE_TASK_TYPE = "summarize_structure_assessment"
_SCORE_PATTERN = re.compile(
    r"\b(?:docking_score|docking score|score)\s*(?:=|:|of|is)?\s*(-?\d+(?:\.\d+)?)\b",
    re.I,
)
_RESIDUE_PATTERN = re.compile(r"\b[A-Za-z0-9]+:[A-Z]{3}\d+[A-Za-z]?\b")
_STRUCTURE_REF_PATTERN = re.compile(
    r"\b(?:RCSB_PDB|AlphaFold_DB|user_supplied|computed_model):[A-Za-z0-9_.-]+\b"
)


@dataclass(frozen=True)
class StructureGuardrailFinding:
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
class StructureGuardrailAuditReport:
    status: StructureValidationStatus
    root_dir: Path
    artifact_count: int
    categories: tuple[str, ...]
    findings: list[StructureGuardrailFinding]

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
class StructureValidationReport:
    status: StructureValidationStatus
    output_dir: Path
    fixture: str
    artifacts: list[str]
    required_steps: list[str]
    guardrail_audit: StructureGuardrailAuditReport

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

    @property
    def is_codex_output(self) -> bool:
        return "codex" in self.relative_path.lower()


def run_structure_validation(
    *,
    output_dir: str | Path = ".molecule-ranker/validation/structure",
    fixture: StructureValidationFixture = "golden",
) -> StructureValidationReport:
    """Run the deterministic V1.3 structure workflow validation."""

    resolved_output = Path(output_dir).resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    _write_structure_validation_workflow(resolved_output, fixture=fixture)
    audit = run_structure_guardrail_audit(resolved_output)
    artifacts = sorted(
        str(path.relative_to(resolved_output))
        for path in resolved_output.rglob("*")
        if path.is_file()
    )
    report = StructureValidationReport(
        status="pass" if audit.status == "pass" else "fail",
        output_dir=resolved_output,
        fixture=fixture,
        artifacts=artifacts,
        required_steps=STRUCTURE_VALIDATION_STEPS,
        guardrail_audit=audit,
    )
    write_json_artifact(resolved_output / "structure_validation_report.json", report.as_dict())
    write_markdown_artifact(
        resolved_output / "structure_validation_report.md",
        "V1.3 Structure Validation Report",
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


def run_structure_guardrail_audit(path: str | Path) -> StructureGuardrailAuditReport:
    root = Path(path).resolve()
    artifacts = _load_artifacts(root)
    refs = _collect_allowed_structure_refs(artifacts)
    findings: list[StructureGuardrailFinding] = []

    for artifact in artifacts:
        findings.extend(_claim_findings(artifact))
        findings.extend(_safety_text_findings(artifact))
        if artifact.json_payload is not None:
            findings.extend(_evidence_separation_findings(artifact))
            findings.extend(_binding_site_provenance_findings(artifact))
            findings.extend(_predicted_structure_findings(artifact))
            findings.extend(_codex_grounding_findings(artifact, refs))
        if artifact.is_codex_output:
            findings.extend(_codex_structure_artifact_findings(artifact, refs))
            findings.extend(_fake_score_findings(artifact, refs))
            findings.extend(_fake_residue_findings(artifact, refs))
            findings.extend(_fake_structure_ref_findings(artifact, refs))

    report = StructureGuardrailAuditReport(
        status="fail" if findings else "pass",
        root_dir=root,
        artifact_count=len(artifacts),
        categories=STRUCTURE_GUARDRAIL_CATEGORIES,
        findings=_dedupe_findings(findings),
    )
    _write_structure_guardrail_audit_reports(report)
    return report


def _write_structure_validation_workflow(
    output_dir: Path,
    *,
    fixture: StructureValidationFixture,
) -> None:
    structures = _synthetic_structures()
    selection = _synthetic_selection()
    receptor = _synthetic_receptor_preparation()
    ligands = _synthetic_ligand_preparation()
    binding_sites = _synthetic_binding_sites()
    docking_runs = _synthetic_docking_runs()
    docking_poses = _synthetic_docking_poses()
    pose_qc = _synthetic_pose_qc()
    interaction_profiles = _synthetic_interaction_profiles()
    assessments = _synthetic_structure_assessments()
    codex_summary = _synthetic_codex_summary()

    if fixture == "fake_binding_site_source":
        binding_sites["binding_sites"][0]["method"] = "known_residues"
        binding_sites["binding_sites"][0]["metadata"] = {
            "provenance": "invented_by_codex",
            "source": "",
        }
    if fixture == "fake_docking_score":
        codex_summary["summary"] = (
            "Codex summary for POSE-GEN1-1 reports docking_score: 0.91 from "
            "DOCK-NULL1."
        )
        codex_summary["reported_docking_score"] = 0.91

    write_json_artifact(output_dir / "structures.json", structures)
    write_json_artifact(output_dir / "structure_selection.json", selection)
    write_json_artifact(output_dir / "receptor_preparation.json", receptor)
    write_json_artifact(output_dir / "ligand_preparation.json", ligands)
    write_json_artifact(output_dir / "binding_sites.json", binding_sites)
    write_json_artifact(output_dir / "docking_runs.json", docking_runs)
    write_json_artifact(output_dir / "docking_poses.json", docking_poses)
    write_json_artifact(output_dir / "pose_qc.json", pose_qc)
    write_json_artifact(output_dir / "interaction_profiles.json", interaction_profiles)
    write_json_artifact(output_dir / "structure_aware_assessments.json", assessments)
    write_json_artifact(output_dir / "codex_structure_summary.json", codex_summary)
    write_json_artifact(output_dir / "structure_validation_trace.json", _validation_trace())
    write_json_artifact(output_dir / "structure_benchmark_report.json", _benchmark_report())
    _write_structure_report(output_dir / "structure_report.md", fixture=fixture)


def _write_structure_report(path: Path, *, fixture: StructureValidationFixture) -> None:
    lines = [
        "## Structure Data Summary",
        "Mock target MOCK1 has one experimental structure and one predicted structure.",
        "Predicted structures are lower-confidence than suitable experimental structures.",
        "",
        "## Structure Selection",
        "Selected structure_id `RCSB_PDB:1ABC` through selection_id `SEL-MOCK1`.",
        "",
        "## Receptor Preparation",
        "Receptor preparation `REC-PREP1` used metadata-only preparation.",
        "",
        "## Binding Site Definition",
        "Binding site `SITE-COCRYSTAL1` is grounded in co-crystal ligand metadata.",
        "",
        "## Ligand Preparation",
        "Generated molecule `GEN-1` remains a computational hypothesis.",
        "",
        "## Docking Summary",
        "Null docking run `DOCK-NULL1` produced normalized docking score: 0.72.",
        "Docking scores do not prove binding.",
        "",
        "## Pose QC Summary",
        "Pose `POSE-GEN1-1` passed synthetic validation checks used by this fixture.",
        "Poses are computational hypotheses.",
        "",
        "## Protein-Ligand Interaction Profiles",
        "Interaction profile `IPL-PROF1` cites residue contact A:LYS33 from the mocked pose.",
        "",
        "## Structure-Aware Assessments",
        "Assessment `SAA-GEN1` supports structure-aware prioritization for human review only.",
        "Generated molecules remain computational hypotheses.",
        "",
        "## Structure Workflow Limitations",
        "Docking scores do not prove binding.",
        "Poses are computational hypotheses.",
        "Predicted structures are lower-confidence than suitable experimental structures.",
        "Generated molecules remain computational hypotheses.",
        "No synthesis instructions.",
        "No lab protocols.",
        "No dosing or patient guidance.",
        "No clinical claims.",
    ]
    if fixture == "overclaim":
        lines.extend(
            [
                "",
                "## Invalid Fixture Text",
                "Docking proves binding and validates the generated molecule from pose alone.",
            ]
        )
    write_markdown_artifact(path, "V1.3 Structure Report", lines)


def _load_artifacts(root: Path) -> list[_ArtifactSnapshot]:
    artifacts: list[_ArtifactSnapshot] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name in IGNORED_FILENAMES:
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        payload = None
        if path.suffix == ".json":
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


def _collect_allowed_structure_refs(artifacts: list[_ArtifactSnapshot]) -> set[str]:
    refs: set[str] = {f"artifact_id:{artifact.relative_path}" for artifact in artifacts}
    for artifact in artifacts:
        payload = artifact.json_payload
        if payload is None:
            continue
        for item in _walk(payload):
            if not isinstance(item, Mapping):
                continue
            _add_ref(refs, "structure_id", item.get("structure_id"))
            _add_ref(refs, "selection_id", item.get("selection_id"))
            _add_ref(refs, "receptor_prep_id", item.get("receptor_prep_id"))
            _add_ref(refs, "docking_run_id", item.get("docking_run_id"))
            _add_ref(refs, "pose_id", item.get("pose_id"))
            _add_ref(refs, "interaction_profile_id", item.get("profile_id"))
            _add_ref(refs, "artifact_id", item.get("artifact_id"))
            if isinstance(item.get("docking_score"), int | float):
                score = _normalize_number(item["docking_score"])
                refs.add(f"structure_score:docking_score:{score}")
                refs.add(f"docking_score:{score}")
                refs.add(f"docking_score={score}")
            for residue in _as_string_list(item.get("key_residue_contacts")):
                refs.add(f"structure_residue:{residue}")
            for residue in _as_string_list(item.get("residues")):
                refs.add(f"structure_residue:{residue}")
    return refs


def _claim_findings(artifact: _ArtifactSnapshot) -> list[StructureGuardrailFinding]:
    findings: list[StructureGuardrailFinding] = []
    overclaim_patterns: tuple[tuple[re.Pattern[str], str, str], ...] = (
        (
            re.compile(
                r"\b(?:docking|pose|structure[- ]?based).{0,80}?\bproves?\s+binding\b",
                re.I,
            ),
            "no_binding_claim_from_docking",
            "Docking or pose text claims binding from structure workflow output.",
        ),
        (
            re.compile(
                r"\b(?:docking|pose|structure[- ]?based).{0,80}?\bconfirms?\s+activity\b",
                re.I,
            ),
            "no_binding_claim_from_docking",
            "Docking or pose text claims activity from structure workflow output.",
        ),
        (
            re.compile(
                r"\bvalidates?\s+(?:the\s+)?generated molecule\b|"
                r"\bgenerated molecule\b.{0,80}\bvalidates?\b",
                re.I,
            ),
            "generated_molecule_not_validated_by_pose",
            "Generated molecule is described as validated by a structure workflow.",
        ),
    )
    for pattern, check_id, message in overclaim_patterns:
        for match in pattern.finditer(artifact.text):
            if _is_negated_safety_statement(artifact.text, match.start()):
                continue
            findings.append(
                StructureGuardrailFinding(
                    "Claim safety",
                    check_id,
                    "error",
                    artifact.relative_path,
                    message,
                    _excerpt(match.group(0)),
                )
            )
    return findings


def _safety_text_findings(artifact: _ArtifactSnapshot) -> list[StructureGuardrailFinding]:
    checks: tuple[tuple[re.Pattern[str], str, str], ...] = (
        (
            re.compile(r"\bsynthesis routes?\b", re.I),
            "no_synthesis_instructions",
            "synthesis route",
        ),
        (
            re.compile(
                r"\breagents?\b.{0,80}\b(?:temperature|solvent|yield|stir|heat)\b",
                re.I | re.S,
            ),
            "no_synthesis_instructions",
            "operational synthesis detail",
        ),
        (re.compile(r"\blab protocols?\b", re.I), "no_lab_protocols", "lab protocol"),
        (
            re.compile(
                r"\bstep[- ]by[- ]step\b.{0,80}\b(?:assay|synthesis|protocol)\b",
                re.I | re.S,
            ),
            "no_lab_protocols",
            "step-by-step protocol",
        ),
        (
            re.compile(r"\b(?:animal|human|patient)\s+dos(?:e|ing)\b", re.I),
            "no_dosing_or_patient_guidance",
            "dosing guidance",
        ),
        (
            re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg/kg|mg per kg|mg/day|mg daily)\b", re.I),
            "no_dosing_or_patient_guidance",
            "dosing amount",
        ),
    )
    findings: list[StructureGuardrailFinding] = []
    for pattern, check_id, label in checks:
        for match in pattern.finditer(artifact.text):
            if _is_negated_safety_statement(artifact.text, match.start()):
                continue
            findings.append(
                StructureGuardrailFinding(
                    "Claim safety",
                    check_id,
                    "error",
                    artifact.relative_path,
                    f"Structure validation output contains prohibited {label}.",
                    _excerpt(match.group(0)),
                )
            )
    return findings


def _evidence_separation_findings(
    artifact: _ArtifactSnapshot,
) -> list[StructureGuardrailFinding]:
    findings: list[StructureGuardrailFinding] = []
    for item in _walk(artifact.json_payload):
        if not isinstance(item, Mapping):
            continue
        lowered_values = " ".join(str(value).lower() for value in item.values())
        lowered_keys = {str(key).lower() for key in item}
        if (
            item.get("record_type") == "EvidenceItem"
            or item.get("type") == "EvidenceItem"
            or (
                "evidence_type" in lowered_keys
                and any(term in lowered_values for term in ("docking", "pose"))
            )
        ):
            findings.append(
                StructureGuardrailFinding(
                    "Evidence separation",
                    "no_docking_score_as_evidence",
                    "error",
                    artifact.relative_path,
                    "Docking or pose output is represented as an EvidenceItem.",
                    _excerpt(lowered_values),
                )
            )
        if (
            any(
                key in lowered_keys
                for key in ("assay_result_id", "experimental_result_id", "result_type")
            )
            and any(term in lowered_values for term in ("docking", "pose"))
        ):
            findings.append(
                StructureGuardrailFinding(
                    "Evidence separation",
                    "no_pose_as_experimental_result",
                    "error",
                    artifact.relative_path,
                    "Docking pose output is represented as an experimental result.",
                    _excerpt(lowered_values),
                )
            )
    return findings


def _binding_site_provenance_findings(
    artifact: _ArtifactSnapshot,
) -> list[StructureGuardrailFinding]:
    if artifact.path.name != "binding_sites.json" or not isinstance(artifact.json_payload, Mapping):
        return []
    findings: list[StructureGuardrailFinding] = []
    for site in artifact.json_payload.get("binding_sites", []):
        if not isinstance(site, Mapping):
            continue
        method = str(site.get("method", ""))
        metadata = site.get("metadata")
        if method in {"co_crystal_ligand", "known_residues", "user_supplied_box"}:
            provenance = ""
            if isinstance(metadata, Mapping):
                provenance = " ".join(
                    str(metadata.get(key, "")) for key in ("source", "provenance", "artifact_id")
                ).strip()
            if not provenance or "invented" in provenance.lower():
                findings.append(
                    StructureGuardrailFinding(
                        "Binding-site provenance",
                        "binding_site_requires_provenance",
                        "error",
                        artifact.relative_path,
                        "Binding-site definition lacks acceptable source provenance.",
                        _excerpt(json.dumps(site, sort_keys=True)),
                    )
                )
    return findings


def _predicted_structure_findings(
    artifact: _ArtifactSnapshot,
) -> list[StructureGuardrailFinding]:
    if artifact.path.name != "structures.json" or not isinstance(artifact.json_payload, Mapping):
        return []
    findings: list[StructureGuardrailFinding] = []
    for structure in artifact.json_payload.get("structures", []):
        if not isinstance(structure, Mapping) or structure.get("structure_type") != "predicted":
            continue
        quality = structure.get("quality_metrics")
        metadata = structure.get("metadata")
        quality_text = (
            json.dumps(quality, sort_keys=True).lower()
            if isinstance(quality, Mapping)
            else ""
        )
        metadata_text = (
            json.dumps(metadata, sort_keys=True).lower() if isinstance(metadata, Mapping) else ""
        )
        if (
            "lower_than_suitable_experimental_structure" not in quality_text
            and "lower-confidence" not in metadata_text
            and "lower_confidence" not in metadata_text
        ):
            findings.append(
                StructureGuardrailFinding(
                    "Predicted-structure labeling",
                    "predicted_structures_labeled_lower_confidence",
                    "error",
                    artifact.relative_path,
                    (
                        "Predicted structure is not labeled lower confidence than suitable "
                        "experimental structures."
                    ),
                    _excerpt(json.dumps(structure, sort_keys=True)),
                )
            )
    return findings


def _codex_grounding_findings(
    artifact: _ArtifactSnapshot,
    refs: set[str],
) -> list[StructureGuardrailFinding]:
    if not artifact.is_codex_output or not isinstance(artifact.json_payload, Mapping):
        return []
    required_keys = {
        "structure_id": "structure_id",
        "selection_id": "selection_id",
        "receptor_prep_id": "receptor_prep_id",
        "docking_run_id": "docking_run_id",
        "pose_id": "pose_id",
        "interaction_profile_id": "interaction_profile_id",
    }
    findings: list[StructureGuardrailFinding] = []
    for key, prefix in required_keys.items():
        value = artifact.json_payload.get(key)
        if not value or f"{prefix}:{value}" not in refs:
            findings.append(
                StructureGuardrailFinding(
                    "Codex output grounding",
                    "codex_output_artifact_grounded",
                    "error",
                    artifact.relative_path,
                    f"Codex structure output is missing or mis-cites {key}.",
                    _excerpt(str(value)),
                )
            )
    artifact_ids = artifact.json_payload.get("artifact_ids")
    if not isinstance(artifact_ids, list) or not artifact_ids:
        findings.append(
            StructureGuardrailFinding(
                "Codex output grounding",
                "codex_output_artifact_grounded",
                "error",
                artifact.relative_path,
                "Codex structure output does not cite artifact IDs.",
                "",
            )
        )
    else:
        for artifact_id in artifact_ids:
            if f"artifact_id:{artifact_id}" not in refs:
                findings.append(
                    StructureGuardrailFinding(
                        "Codex output grounding",
                        "codex_output_artifact_grounded",
                        "error",
                        artifact.relative_path,
                        "Codex structure output cites an unknown artifact ID.",
                        _excerpt(str(artifact_id)),
                    )
                )
    return findings


def _codex_structure_artifact_findings(
    artifact: _ArtifactSnapshot,
    refs: set[str],
) -> list[StructureGuardrailFinding]:
    result = CodexTaskResult(
        task_id="structure-validation-audit",
        task_type=_STRUCTURE_TASK_TYPE,  # type: ignore[arg-type]
        status="succeeded",
        output_text=artifact.text,
        output_json=artifact.json_payload if isinstance(artifact.json_payload, dict) else None,
    )
    findings: list[StructureGuardrailFinding] = []
    for warning in detect_structure_artifact_violations(result, refs):
        check_id = "codex_output_artifact_grounded"
        category = "Codex output grounding"
        if "docking score" in warning.lower():
            check_id = "no_fake_docking_scores"
            category = "Structure artifact grounding"
        elif "residue contact" in warning.lower():
            check_id = "no_fake_residues"
            category = "Structure artifact grounding"
        elif "claim" in warning.lower():
            check_id = "no_binding_claim_from_docking"
            category = "Claim safety"
        findings.append(
            StructureGuardrailFinding(
                category,
                check_id,
                "error",
                artifact.relative_path,
                warning,
                "",
            )
        )
    return findings


def _fake_score_findings(
    artifact: _ArtifactSnapshot,
    refs: set[str],
) -> list[StructureGuardrailFinding]:
    allowed = {ref.lower() for ref in refs}
    findings: list[StructureGuardrailFinding] = []
    for match in _SCORE_PATTERN.finditer(artifact.text):
        raw_value = _normalize_number(float(match.group(1)))
        candidates = {
            f"structure_score:docking_score:{raw_value}",
            f"docking_score:{raw_value}",
            f"docking_score={raw_value}",
        }
        if candidates.isdisjoint(allowed):
            findings.append(
                StructureGuardrailFinding(
                    "Structure artifact grounding",
                    "no_fake_docking_scores",
                    "error",
                    artifact.relative_path,
                    "Codex output references a docking score not present in structure artifacts.",
                    _excerpt(match.group(0)),
                )
            )
    return findings


def _fake_residue_findings(
    artifact: _ArtifactSnapshot,
    refs: set[str],
) -> list[StructureGuardrailFinding]:
    allowed_residues = {
        ref.removeprefix("structure_residue:")
        for ref in {item.lower() for item in refs}
        if ref.startswith("structure_residue:")
    }
    findings: list[StructureGuardrailFinding] = []
    for match in _RESIDUE_PATTERN.finditer(artifact.text):
        residue = match.group(0)
        if residue.lower() not in allowed_residues:
            findings.append(
                StructureGuardrailFinding(
                    "Structure artifact grounding",
                    "no_fake_residues",
                    "error",
                    artifact.relative_path,
                    "Codex output references a residue contact not present in artifacts.",
                    residue,
                )
            )
    return findings


def _fake_structure_ref_findings(
    artifact: _ArtifactSnapshot,
    refs: set[str],
) -> list[StructureGuardrailFinding]:
    allowed_structures = {
        ref.removeprefix("structure_id:")
        for ref in refs
        if ref.startswith("structure_id:")
    }
    findings: list[StructureGuardrailFinding] = []
    for match in _STRUCTURE_REF_PATTERN.finditer(artifact.text):
        structure_ref = match.group(0)
        if structure_ref not in allowed_structures:
            findings.append(
                StructureGuardrailFinding(
                    "Structure artifact grounding",
                    "no_fake_structures",
                    "error",
                    artifact.relative_path,
                    "Codex output references a structure not present in artifacts.",
                    structure_ref,
                )
            )
    return findings


def _write_structure_guardrail_audit_reports(report: StructureGuardrailAuditReport) -> None:
    report.root_dir.mkdir(parents=True, exist_ok=True)
    write_json_artifact(report.root_dir / "structure_guardrail_audit.json", report.as_dict())
    write_markdown_artifact(
        report.root_dir / "structure_guardrail_audit.md",
        "V1.3 Structure Guardrail Audit",
        [
            f"- Status: `{report.status}`",
            f"- Artifact count: {report.artifact_count}",
            f"- Finding count: {len(report.findings)}",
            "",
            "## Findings",
            *(
                [
                    "- None",
                ]
                if not report.findings
                else [
                    (
                        f"- `{finding.check_id}` ({finding.severity}) in "
                        f"`{finding.artifact_path}`: {finding.message}"
                    )
                    for finding in report.findings
                ]
            ),
        ],
    )


def _synthetic_structures() -> dict[str, Any]:
    now = _timestamp()
    return {
        "structures": [
            {
                "structure_id": "RCSB_PDB:1ABC",
                "source": "RCSB_PDB",
                "external_id": "1ABC",
                "target_symbol": "MOCK1",
                "target_identifiers": {"uniprot": "P0MOCK", "gene_symbol": "MOCK1"},
                "structure_type": "experimental",
                "experimental_method": "X-ray diffraction",
                "resolution_angstrom": 1.8,
                "coverage": {"chain_A": {"start": 1, "end": 250, "fraction": 0.94}},
                "chains": ["A"],
                "ligands": [
                    {
                        "ligand_id": "LIG",
                        "chain_id": "A",
                        "role": "co_crystal_reference",
                        "coordinate_source": "mocked_structure_metadata",
                    }
                ],
                "mutations": [],
                "organism": "Homo sapiens",
                "release_date": "2024-01-01",
                "quality_metrics": {"target_mapping_confidence": 0.98},
                "url": "https://www.rcsb.org/structure/1ABC",
                "retrieved_at": now,
                "metadata": {"artifact_id": "structures.json", "mocked": True},
            },
            {
                "structure_id": "AlphaFold_DB:AF-P0MOCK-F1",
                "source": "AlphaFold_DB",
                "external_id": "AF-P0MOCK-F1",
                "target_symbol": "MOCK1",
                "target_identifiers": {"uniprot": "P0MOCK", "gene_symbol": "MOCK1"},
                "structure_type": "predicted",
                "experimental_method": None,
                "resolution_angstrom": None,
                "coverage": {"model": {"start": 1, "end": 250, "fraction": 1.0}},
                "chains": ["A"],
                "ligands": [],
                "mutations": [],
                "organism": "Homo sapiens",
                "release_date": "2024-01-01",
                "quality_metrics": {
                    "mean_plddt": 88.0,
                    "binding_region_plddt": 75.0,
                    "relative_confidence": "lower_than_suitable_experimental_structure",
                },
                "url": "https://alphafold.ebi.ac.uk/entry/P0MOCK",
                "retrieved_at": now,
                "metadata": {
                    "artifact_id": "structures.json",
                    "mocked": True,
                    "confidence_label": "lower-confidence predicted structure",
                },
            },
        ]
    }


def _synthetic_selection() -> dict[str, Any]:
    return {
        "selection_id": "SEL-MOCK1",
        "target_symbol": "MOCK1",
        "selected_structure_id": "RCSB_PDB:1ABC",
        "selected_chain_ids": ["A"],
        "selection_reason": "Experimental co-crystal structure with a reference ligand.",
        "confidence": 0.86,
        "rejected_structures": [
            {
                "structure_id": "AlphaFold_DB:AF-P0MOCK-F1",
                "reason": (
                    "Predicted structure is lower confidence than suitable experimental "
                    "co-crystal structure."
                ),
            }
        ],
        "warnings": [
            "Predicted structures are not equivalent to experimental co-crystal structures."
        ],
        "metadata": {"artifact_id": "structure_selection.json"},
    }


def _synthetic_receptor_preparation() -> dict[str, Any]:
    return {
        "receptor_preparations": [
            {
                "receptor_prep_id": "REC-PREP1",
                "structure_id": "RCSB_PDB:1ABC",
                "target_symbol": "MOCK1",
                "input_structure_path": "structures/mock_1abc.pdb",
                "prepared_receptor_path": None,
                "preparation_method": "metadata_only",
                "protonation_policy": "not_applied_metadata_only",
                "kept_chains": ["A"],
                "removed_chains": [],
                "kept_heterogens": ["LIG"],
                "removed_heterogens": ["HOH"],
                "missing_atoms_fixed": False,
                "missing_hydrogens_added": False,
                "missing_loops_modeled": False,
                "alternate_locations_resolved": False,
                "warnings": [
                    (
                        "Metadata-only receptor preparation is computational bookkeeping, "
                        "not experimental evidence."
                    )
                ],
                "confidence": 0.72,
                "metadata": {
                    "artifact_id": "receptor_preparation.json",
                    "input_structure_hash": "sha256:mocked",
                },
            }
        ]
    }


def _synthetic_ligand_preparation() -> dict[str, Any]:
    return {
        "ligand_preparations": [
            {
                "ligand_prep_id": "LIGPREP-GEN1",
                "molecule_id": "GEN-1",
                "molecule_name": "Generated validation molecule",
                "origin": "generated",
                "canonical_smiles": "CCO",
                "conformer_count": 2,
                "prepared_ligand_paths": ["ligands/GEN-1_conf0.sdf", "ligands/GEN-1_conf1.sdf"],
                "charge_method": None,
                "protonation_policy": "unchanged_validation_fixture",
                "stereochemistry_status": "specified",
                "warnings": ["Generated molecule remains a computational hypothesis."],
                "confidence": 0.8,
                "metadata": {"artifact_id": "ligand_preparation.json"},
            }
        ]
    }


def _synthetic_binding_sites() -> dict[str, Any]:
    return {
        "binding_sites": [
            {
                "binding_site_id": "SITE-COCRYSTAL1",
                "target_symbol": "MOCK1",
                "structure_id": "RCSB_PDB:1ABC",
                "method": "co_crystal_ligand",
                "center": [1.0, 2.0, 3.0],
                "box_size": [18.0, 18.0, 18.0],
                "residues": ["A:LYS33", "A:ASP77"],
                "reference_ligand_id": "LIG",
                "confidence": 0.84,
                "warnings": ["Binding-site box is mocked for validation only."],
                "metadata": {
                    "artifact_id": "binding_sites.json",
                    "source": "structures.json",
                    "provenance": "mock co-crystal ligand coordinates from RCSB_PDB:1ABC",
                },
            }
        ]
    }


def _synthetic_docking_runs() -> dict[str, Any]:
    return {
        "docking_runs": [
            {
                "docking_run_id": "DOCK-NULL1",
                "target_symbol": "MOCK1",
                "structure_id": "RCSB_PDB:1ABC",
                "receptor_prep_id": "REC-PREP1",
                "binding_site_id": "SITE-COCRYSTAL1",
                "docking_engine": "null",
                "docking_engine_version": "validation",
                "config": {
                    "enable_structure_docking": False,
                    "mocked": True,
                    "docking_random_seed": 13,
                },
                "started_at": _timestamp(),
                "completed_at": _timestamp(),
                "status": "succeeded",
                "ligand_count": 1,
                "pose_count": 1,
                "artifacts": {"poses": "docking_poses.json"},
                "warnings": [
                    (
                        "Null docking is deterministic validation output and is not "
                        "experimental evidence."
                    )
                ],
                "metadata": {
                    "artifact_id": "docking_runs.json",
                    "not_evidence_item": True,
                    "not_experimental_result": True,
                },
            }
        ]
    }


def _synthetic_docking_poses() -> dict[str, Any]:
    return {
        "docking_poses": [
            {
                "pose_id": "POSE-GEN1-1",
                "docking_run_id": "DOCK-NULL1",
                "molecule_id": "GEN-1",
                "molecule_name": "Generated validation molecule",
                "canonical_smiles": "CCO",
                "target_symbol": "MOCK1",
                "structure_id": "RCSB_PDB:1ABC",
                "binding_site_id": "SITE-COCRYSTAL1",
                "pose_rank": 1,
                "docking_score": 0.72,
                "score_units": "normalized_unitless",
                "pose_path": None,
                "interaction_summary": {"profile_id": "IPL-PROF1"},
                "pose_quality": {"passes_qc": True, "within_binding_site": True, "score": 0.78},
                "confidence": 0.62,
                "warnings": ["Docking scores do not prove binding."],
                "metadata": {
                    "artifact_id": "docking_poses.json",
                    "not_evidence_item": True,
                    "not_experimental_result": True,
                },
            }
        ]
    }


def _synthetic_pose_qc() -> dict[str, Any]:
    return {
        "pose_qc": [
            {
                "pose_id": "POSE-GEN1-1",
                "checks": {
                    "score_present": True,
                    "ligand_within_binding_site_box": True,
                    "severe_clash": False,
                },
                "confidence_adjustment": -0.05,
                "warnings": ["Pose QC does not prove binding."],
                "metadata": {"artifact_id": "pose_qc.json"},
            }
        ]
    }


def _synthetic_interaction_profiles() -> dict[str, Any]:
    return {
        "interaction_profiles": [
            {
                "profile_id": "IPL-PROF1",
                "pose_id": "POSE-GEN1-1",
                "target_symbol": "MOCK1",
                "molecule_id": "GEN-1",
                "interactions": [
                    {
                        "interaction_type": "hydrogen_bond_like",
                        "residue": "A:LYS33",
                        "distance_angstrom": 3.0,
                        "method": "mocked_geometric_heuristic",
                    }
                ],
                "interaction_counts": {"hydrogen_bond_like": 1, "hydrophobic": 0},
                "key_residue_contacts": ["A:LYS33"],
                "reference_similarity": 0.42,
                "warnings": [
                    "Interactions are computational pose annotations, not experimental evidence."
                ],
                "confidence": 0.58,
                "metadata": {"artifact_id": "interaction_profiles.json"},
            }
        ]
    }


def _synthetic_structure_assessments() -> dict[str, Any]:
    return {
        "structure_aware_assessments": [
            {
                "assessment_id": "SAA-GEN1",
                "molecule_id": "GEN-1",
                "molecule_name": "Generated validation molecule",
                "target_symbol": "MOCK1",
                "structure_id": "RCSB_PDB:1ABC",
                "docking_pose_ids": ["POSE-GEN1-1"],
                "structure_score": 0.62,
                "pose_confidence": 0.62,
                "interaction_score": 0.55,
                "consensus_score": 0.6,
                "applicability_domain": "suitable_experimental_structure",
                "recommendation": "needs_structure_review",
                "warnings": [
                    "Generated molecule remains a computational hypothesis.",
                    "Consensus score is not predicted binding affinity.",
                ],
                "explanation": (
                    "Structure-aware prioritization only; human review remains required."
                ),
                "metadata": {
                    "artifact_id": "structure_aware_assessments.json",
                    "not_evidence_item": True,
                    "not_validated": True,
                },
            }
        ]
    }


def _synthetic_codex_summary() -> dict[str, Any]:
    return {
        "codex_task_result_id": "CODEX-STRUCTURE-SUMMARY-1",
        "task_type": _STRUCTURE_TASK_TYPE,
        "summary": (
            "Artifact-grounded structure summary for RCSB_PDB:1ABC, SEL-MOCK1, "
            "REC-PREP1, DOCK-NULL1, POSE-GEN1-1, and IPL-PROF1. "
            "The docking_score: 0.72 is a weak computational signal and not proof of binding. "
            "Contact A:LYS33 is a mocked pose annotation."
        ),
        "structure_id": "RCSB_PDB:1ABC",
        "selection_id": "SEL-MOCK1",
        "receptor_prep_id": "REC-PREP1",
        "docking_run_id": "DOCK-NULL1",
        "pose_id": "POSE-GEN1-1",
        "interaction_profile_id": "IPL-PROF1",
        "artifact_ids": [
            "structures.json",
            "structure_selection.json",
            "receptor_preparation.json",
            "binding_sites.json",
            "docking_runs.json",
            "docking_poses.json",
            "interaction_profiles.json",
            "structure_aware_assessments.json",
        ],
        "metadata": {"artifact_id": "codex_structure_summary.json"},
    }


def _validation_trace() -> dict[str, Any]:
    return {
        "workflow_id": "v1.3_structure_validation",
        "required_steps": [
            {"name": step, "status": "completed"} for step in STRUCTURE_VALIDATION_STEPS
        ],
        "guardrails": [
            "no docking score becomes EvidenceItem",
            "no pose becomes experimental result",
            "no binding claim from docking",
            "no generated molecule validated from pose alone",
            "no synthesis instructions",
            "no lab protocols",
            "no dosing",
            "no fake residues/structures/scores",
            "predicted structures labeled lower confidence",
            "Codex output artifact-grounded",
        ],
    }


def _benchmark_report() -> dict[str, Any]:
    return {
        "metrics": {
            "structures_found_per_target": {"MOCK1": 2},
            "receptor_prep_success_rate": 1.0,
            "ligand_prep_success_rate": 1.0,
            "docking_success_rate": 1.0,
            "pose_qc_pass_rate": 1.0,
            "predicted_vs_experimental_structure_usage": {
                "experimental": 1,
                "predicted": 0,
            },
        },
        "redocking_benchmark": {"status": "skipped", "reason": "Synthetic validation fixture."},
    }


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, Mapping):
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def _add_ref(refs: set[str], prefix: str, value: Any) -> None:
    if isinstance(value, str) and value:
        refs.add(f"{prefix}:{value}")


def _as_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _normalize_number(value: float) -> str:
    normalized = f"{value:.6f}".rstrip("0").rstrip(".")
    return normalized or "0"


def _excerpt(text: str, *, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _is_negated_safety_statement(text: str, start: int) -> bool:
    prefix = text[max(0, start - 32) : start].lower()
    prefix = re.split(r"[.\n;:]", prefix)[-1].strip(" \t\"'[]{}(),")
    suffix = text[start : start + 96].lower()
    return (
        prefix in {"no", "not", "do not", "does not", "cannot", "must not"}
        or prefix.endswith((" no", " not", " do not", " does not", " cannot", " must not"))
        or "does not prove binding" in suffix
        or "do not prove binding" in suffix
        or "not proof of binding" in suffix
    )


def _dedupe_findings(
    findings: list[StructureGuardrailFinding],
) -> list[StructureGuardrailFinding]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[StructureGuardrailFinding] = []
    for finding in findings:
        key = (finding.check_id, finding.artifact_path, finding.message, finding.excerpt)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)
    return deduped


def _timestamp() -> str:
    return datetime(2026, 5, 30, 12, 0, tzinfo=UTC).isoformat()


__all__ = [
    "STRUCTURE_GUARDRAIL_CATEGORIES",
    "STRUCTURE_VALIDATION_STEPS",
    "StructureGuardrailAuditReport",
    "StructureGuardrailFinding",
    "StructureValidationFixture",
    "StructureValidationReport",
    "run_structure_guardrail_audit",
    "run_structure_validation",
]
