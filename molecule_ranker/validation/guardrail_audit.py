from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

GuardrailAuditStatus = Literal["pass", "fail"]

AUDIT_CATEGORIES = (
    "Biomedical evidence integrity",
    "Generated molecule integrity",
    "Literature/citation integrity",
    "Experimental result integrity",
    "Codex output integrity",
    "External integration integrity",
    "Review decision separation",
    "Medical/synthesis/lab-protocol safety",
)

IGNORED_FILENAMES = {"guardrail_audit.json", "guardrail_audit.md"}


@dataclass(frozen=True)
class GuardrailFinding:
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
class GuardrailAuditReport:
    status: GuardrailAuditStatus
    root_dir: Path
    artifact_count: int
    categories: tuple[str, ...]
    findings: list[GuardrailFinding]

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
class ArtifactSnapshot:
    path: Path
    relative_path: str
    text: str
    json_payload: Any | None = None

    @property
    def is_codex(self) -> bool:
        lowered = self.relative_path.lower()
        if "codex" in lowered:
            return True
        payload = self.json_payload
        return isinstance(payload, dict) and str(payload.get("artifact_type", "")).startswith(
            "codex"
        )

    @property
    def is_generated(self) -> bool:
        lowered = self.relative_path.lower()
        return "generated" in lowered or "generation" in lowered

    @property
    def is_integration(self) -> bool:
        lowered = self.relative_path.lower()
        return "integration" in lowered or "sync" in lowered or "external_system" in lowered


def run_guardrail_audit(path: str | Path) -> GuardrailAuditReport:
    """Audit local artifacts for V1.0 scientific-integrity guardrail violations."""

    root = Path(path).resolve()
    artifacts = _load_artifacts(root)
    imported_results = _imported_result_index(artifacts)
    findings: list[GuardrailFinding] = []
    for artifact in artifacts:
        findings.extend(_text_findings(artifact))
        findings.extend(_json_findings(artifact, imported_results=imported_results))
    report = GuardrailAuditReport(
        status="fail" if findings else "pass",
        root_dir=root,
        artifact_count=len(artifacts),
        categories=AUDIT_CATEGORIES,
        findings=findings,
    )
    write_guardrail_audit_reports(report)
    return report


def write_guardrail_audit_reports(report: GuardrailAuditReport) -> None:
    report.root_dir.mkdir(parents=True, exist_ok=True)
    (report.root_dir / "guardrail_audit.json").write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True) + "\n"
    )
    (report.root_dir / "guardrail_audit.md").write_text(_render_markdown(report))


def _load_artifacts(root: Path) -> list[ArtifactSnapshot]:
    if not root.exists() or not root.is_dir():
        return []
    artifacts: list[ArtifactSnapshot] = []
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
            ArtifactSnapshot(
                path=path,
                relative_path=path.relative_to(root).as_posix(),
                text=text,
                json_payload=payload,
            )
        )
    return artifacts


def _text_findings(artifact: ArtifactSnapshot) -> list[GuardrailFinding]:
    rules = [
        (
            "Literature/citation integrity",
            "no_fake_citations",
            re.compile(
                r"\b(?:fake|fabricated|invented)\s+citation\b|"
                r"\bPMID\s*:?\s*(?:0{4,}|9{6,})\b|"
                r"\bdoi\s*:?\s*10\.0000/[A-Za-z0-9_.-]+",
                re.I,
            ),
            "Artifact contains a fake or unsupported citation marker.",
        ),
        (
            "Experimental result integrity",
            "no_fake_assay_results",
            re.compile(r"\b(?:fake|fabricated|invented)\s+assay\s+result\b", re.I),
            "Artifact contains a fake assay-result claim.",
        ),
        (
            "Generated molecule integrity",
            "no_generated_molecule_called_active",
            re.compile(r"\b(?:validated|known|confirmed)\s+active\b|\bactive\s+compound\b", re.I),
            "Generated molecule output calls a molecule active.",
        ),
        (
            "Biomedical evidence integrity",
            "no_docking_score_as_binding_proof",
            re.compile(
                r"\bdocking\s+score\b.{0,80}\b(?:proves?|proof|demonstrates?)\b.{0,40}\bbinding\b",
                re.I | re.S,
            ),
            "Docking score is described as proof of binding.",
        ),
        (
            "Biomedical evidence integrity",
            "no_admet_as_safety_proof",
            re.compile(
                r"\bADMET\b.{0,80}\b(?:proves?|proof|demonstrates?)\b.{0,40}\bsafety\b",
                re.I | re.S,
            ),
            "ADMET output is described as proof of safety.",
        ),
        (
            "Biomedical evidence integrity",
            "no_model_prediction_as_evidence",
            re.compile(
                r"\bmodel\s+prediction\b.{0,80}\b(?:is|as|becomes?|counts?\s+as)\b"
                r".{0,40}\bevidence\b",
                re.I | re.S,
            ),
            "Model prediction is described as evidence.",
        ),
        (
            "Medical/synthesis/lab-protocol safety",
            "no_synthesis_instructions",
            re.compile(
                r"\b(?:step[- ]?by[- ]?step\s+)?synthesis\s+(?:route|steps?)\b|"
                r"\bsynthesis\s+instructions?\s*:",
                re.I,
            ),
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
            "no_dosing_or_patient_treatment_guidance",
            re.compile(r"\bdos(?:e|ing)\s+guidance\b|\bpatient\s+treatment\b", re.I),
            "Artifact contains dosing or patient-treatment guidance.",
        ),
        (
            "Medical/synthesis/lab-protocol safety",
            "no_cure_treat_claims",
            re.compile(
                r"\bcures?\b|"
                r"\b(?:treats?|prevents?)\b[^.\n]{0,80}"
                r"\b(?:disease|condition|patient|tumou?r|cancer|exampledisease)\b|"
                r"\b(?:disease|condition|patient|tumou?r|cancer|exampledisease)\b"
                r"[^.\n]{0,80}\b(?:treated|prevented)\b",
                re.I,
            ),
            "Artifact contains cure, treatment, or prevention claim language.",
        ),
    ]
    findings: list[GuardrailFinding] = []
    for category, check_id, pattern, message in rules:
        for match in pattern.finditer(artifact.text):
            if _is_allowed_negated_context(artifact.text, match.start()):
                continue
            if check_id == "no_generated_molecule_called_active" and not artifact.is_generated:
                continue
            active_category = (
                "Codex output integrity"
                if check_id == "no_fake_citations" and artifact.is_codex
                else category
            )
            findings.append(_finding(artifact, active_category, check_id, message, match))
    return findings


def _json_findings(
    artifact: ArtifactSnapshot,
    *,
    imported_results: dict[str, set[str]],
) -> list[GuardrailFinding]:
    payload = artifact.json_payload
    if payload is None:
        return []
    findings: list[GuardrailFinding] = []
    if artifact.is_codex:
        findings.extend(_codex_findings(artifact, payload))
    if artifact.is_generated:
        findings.extend(_generated_findings(artifact, payload, imported_results=imported_results))
    findings.extend(_citation_findings(artifact, payload))
    findings.extend(_experimental_findings(artifact, payload))
    findings.extend(_review_findings(artifact, payload))
    if artifact.is_integration:
        findings.extend(_integration_findings(artifact, payload))
    return findings


def _codex_findings(artifact: ArtifactSnapshot, payload: Any) -> list[GuardrailFinding]:
    findings = []
    if _contains_key(payload, "EvidenceItem") or _contains_key(payload, "evidence_item"):
        findings.append(
            _structural_finding(
                artifact,
                "Codex output integrity",
                "no_codex_output_promoted_to_evidence",
                "Codex output attempts to create or promote an EvidenceItem.",
                "EvidenceItem",
            )
        )
    if _contains_key(payload, "evidence") or _contains_key(payload, "evidence_type"):
        findings.append(
            _structural_finding(
                artifact,
                "Codex output integrity",
                "no_codex_output_promoted_to_evidence",
                "Codex output contains biomedical evidence fields.",
                "evidence",
            )
        )
    return findings


def _generated_findings(
    artifact: ArtifactSnapshot,
    payload: Any,
    *,
    imported_results: dict[str, set[str]],
) -> list[GuardrailFinding]:
    findings: list[GuardrailFinding] = []
    for molecule in _generated_molecules(payload):
        molecule_id = str(
            molecule.get("generated_id")
            or molecule.get("candidate_id")
            or molecule.get("molecule_id")
            or ""
        )
        if _generated_active_claim(molecule):
            findings.append(
                _structural_finding(
                    artifact,
                    "Generated molecule integrity",
                    "no_generated_molecule_called_active",
                    "Generated molecule is labeled as active or validated.",
                    molecule_id or "generated molecule",
                )
            )
        evidence_items = molecule.get("evidence")
        if isinstance(evidence_items, list) and evidence_items:
            allowed = imported_results.get(molecule_id, set()) if molecule_id else set()
            for evidence in evidence_items:
                result_ref = _result_reference(evidence)
                if not result_ref or result_ref not in allowed:
                    findings.append(
                        _structural_finding(
                            artifact,
                            "Generated molecule integrity",
                            "no_generated_direct_evidence_without_imported_result",
                            (
                                "Generated molecule has direct evidence without an exact "
                                "imported experimental result."
                            ),
                            result_ref or molecule_id or "generated evidence",
                        )
                    )
    return findings


def _citation_findings(artifact: ArtifactSnapshot, payload: Any) -> list[GuardrailFinding]:
    findings: list[GuardrailFinding] = []
    for item in _dicts(payload):
        keys = set(item)
        citation_like = bool(keys & {"citation", "citation_id", "pmid", "doi"}) or (
            "title" in keys
            and str(item.get("evidence_type") or item.get("source") or "").lower()
            in {"literature", "publication", "pubmed", "citation"}
        )
        if citation_like and not item.get("source_record_id") and not item.get("record_id"):
            findings.append(
                _structural_finding(
                    artifact,
                    "Literature/citation integrity",
                    "no_fake_citations",
                    "Citation-like record is missing source_record_id.",
                    str(item.get("title") or item.get("pmid") or item.get("doi") or "citation"),
                )
            )
    return findings


def _experimental_findings(artifact: ArtifactSnapshot, payload: Any) -> list[GuardrailFinding]:
    findings: list[GuardrailFinding] = []
    lowered = artifact.relative_path.lower()
    if "experimental" in lowered or "assay" in lowered or "import" in lowered:
        for item in _dicts(payload):
            if "results" in item or "result_id" in item:
                source_type = item.get("source_type") or item.get("source")
                if source_type and str(source_type).lower() not in {
                    "file",
                    "synthetic_csv",
                    "assay_results.csv",
                }:
                    findings.append(
                        _structural_finding(
                            artifact,
                            "Experimental result integrity",
                            "experimental_results_imported_only_from_files",
                            "Experimental results must be imported only from files.",
                            str(source_type),
                        )
                    )
            if item.get("qc_status") == "failed" and (
                bool(item.get("improved_score")) or bool(item.get("supports_score_increase"))
            ):
                findings.append(
                    _structural_finding(
                        artifact,
                        "Experimental result integrity",
                        "failed_qc_does_not_improve_scores",
                        "Failed QC result is marked as score-improving.",
                        str(item.get("result_id") or "failed_qc"),
                    )
                )
    return findings


def _review_findings(artifact: ArtifactSnapshot, payload: Any) -> list[GuardrailFinding]:
    findings: list[GuardrailFinding] = []
    for item in _dicts(payload):
        evidence_type = str(item.get("evidence_type", "")).lower()
        source = str(item.get("source", "")).lower()
        if evidence_type in {"review_decision", "review", "expert_review"} or source in {
            "review",
            "review_decision",
        }:
            findings.append(
                _structural_finding(
                    artifact,
                    "Review decision separation",
                    "no_review_decision_stored_as_biomedical_evidence",
                    "Review decision is stored as biomedical evidence.",
                    str(item.get("title") or item.get("decision") or "review evidence"),
                )
            )
    return findings


def _integration_findings(artifact: ArtifactSnapshot, payload: Any) -> list[GuardrailFinding]:
    findings: list[GuardrailFinding] = []
    for item in _dicts(payload):
        if item.get("external_write") is True:
            findings.append(
                _structural_finding(
                    artifact,
                    "External integration integrity",
                    "external_sync_dry_run_by_default",
                    "External integration artifact performs writes instead of dry-run validation.",
                    "external_write",
                )
            )
        if item.get("dry_run") is False or item.get("mode") in {"write", "live_write"}:
            findings.append(
                _structural_finding(
                    artifact,
                    "External integration integrity",
                    "external_sync_dry_run_by_default",
                    "External integration sync is not dry-run by default.",
                    str(item.get("mode") or "dry_run=false"),
                )
            )
    return findings


def _imported_result_index(artifacts: Iterable[ArtifactSnapshot]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for artifact in artifacts:
        if artifact.json_payload is None:
            continue
        lowered = artifact.relative_path.lower()
        if "experimental" not in lowered and "assay" not in lowered:
            continue
        for item in _dicts(artifact.json_payload):
            candidate_id = item.get("generated_id") or item.get("candidate_id") or item.get(
                "molecule_id"
            )
            result_id = _result_reference(item)
            if candidate_id and result_id and str(item.get("qc_status", "")).lower() != "failed":
                index.setdefault(str(candidate_id), set()).add(result_id)
    return index


def _generated_molecules(payload: Any) -> Iterable[dict[str, Any]]:
    for item in _dicts(payload):
        if item.get("origin") == "generated" or item.get("is_generated") is True:
            yield item
        elif item.get("generated_id") and (
            "label" in item or "evidence" in item or "validated_active" in item
        ):
            yield item


def _generated_active_claim(item: dict[str, Any]) -> bool:
    if item.get("validated_active") is True or item.get("is_active") is True:
        return True
    text_fields = " ".join(
        str(item.get(key, ""))
        for key in ("label", "status", "claim", "summary", "description")
        if item.get(key)
    )
    active_pattern = r"\b(?:validated|known|confirmed)\s+active\b|\bactive\s+compound\b"
    return bool(re.search(active_pattern, text_fields, re.I))


def _result_reference(item: Any) -> str | None:
    if not isinstance(item, dict):
        return None
    for key in ("result_id", "source_result_id", "experimental_result_id", "source_record_id"):
        value = item.get(key)
        if value:
            return str(value)
    return None


def _contains_key(payload: Any, key: str) -> bool:
    if isinstance(payload, dict):
        return any(
            item_key == key or _contains_key(value, key)
            for item_key, value in payload.items()
        )
    if isinstance(payload, list):
        return any(_contains_key(item, key) for item in payload)
    return False


def _dicts(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, dict):
        yield payload
        for value in payload.values():
            yield from _dicts(value)
    elif isinstance(payload, list):
        for item in payload:
            yield from _dicts(item)


def _is_allowed_negated_context(text: str, start: int) -> bool:
    before = text[max(0, start - 80) : start].lower()
    return bool(
        re.search(
            r"\b(?:no|not|never|without|does\s+not|do\s+not|cannot|is\s+not|are\s+not)\b"
            r"[^.\n;:]{0,80}$",
            before,
        )
        or re.search(
            r"\b(?:omit|omits|omitted|exclude|excludes|excluded|excluding)\b"
            r"[^.\n;:]{0,80}$",
            before,
        )
    )


def _finding(
    artifact: ArtifactSnapshot,
    category: str,
    check_id: str,
    message: str,
    match: re.Match[str],
) -> GuardrailFinding:
    return GuardrailFinding(
        category=category,
        check_id=check_id,
        severity="error",
        artifact_path=artifact.relative_path,
        message=message,
        excerpt=_excerpt(artifact.text, match.start(), match.end()),
    )


def _structural_finding(
    artifact: ArtifactSnapshot,
    category: str,
    check_id: str,
    message: str,
    excerpt: str,
) -> GuardrailFinding:
    return GuardrailFinding(
        category=category,
        check_id=check_id,
        severity="error",
        artifact_path=artifact.relative_path,
        message=message,
        excerpt=excerpt,
    )


def _excerpt(text: str, start: int, end: int) -> str:
    return " ".join(text[max(0, start - 80) : end + 80].split())


def _render_markdown(report: GuardrailAuditReport) -> str:
    lines = [
        "# V1.0 Guardrail Audit",
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
        lines.append("No guardrail violations found.")
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
