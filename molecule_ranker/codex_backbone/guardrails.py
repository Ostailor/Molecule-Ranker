from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask, CodexTaskResult

STRUCTURE_CODEX_TASK_TYPES = {
    "suggest_structure_selection_review_questions",
    "summarize_structure_assessment",
    "explain_pose_qc_failure",
    "draft_structure_report_summary",
    "plan_followup_structure_workflow",
}
STRUCTURE_REQUIRED_REF_PREFIXES = {
    "structure_id": "structure_id",
    "selection_id": "selection_id",
    "receptor_prep_id": "receptor_prep_id",
    "docking_run_id": "docking_run_id",
    "pose_id": "pose_id",
    "interaction_profile_id": "interaction_profile_id",
    "artifact_id": "artifact IDs",
}
STRUCTURE_SCORE_PATTERN = re.compile(
    r"\b(?:docking_score|docking score|score)\s*(?:=|:|of|is)?\s*(-?\d+(?:\.\d+)?)\b",
    re.I,
)
STRUCTURE_RESIDUE_PATTERN = re.compile(r"\b[A-Za-z0-9]+:[A-Z]{3}\d+[A-Za-z]?\b")
STRUCTURE_OVERCLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b(?:docking|pose|structure[- ]?based).{0,80}\bproves?\s+binding\b", re.I),
        "docking or pose proves binding",
    ),
    (
        re.compile(r"\b(?:docking|pose|structure[- ]?based).{0,80}\bconfirms?\s+activity\b", re.I),
        "docking or pose confirms activity",
    ),
    (
        re.compile(r"\b(?:validated|active|binder)\s+from\s+(?:docking|pose|structure)\b", re.I),
        "validated activity or binding from structure workflow",
    ),
)

SECRET_FILE_MARKERS = (
    ".env",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "token",
    ".pem",
    ".p12",
    ".pfx",
)

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(api[_-]?key|openai[_-]?api[_-]?key|secret|token|password|passwd|"
            r"authorization)\s*[:=]\s*([^\s\"']{6,})"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)([\"']?(?:api[_-]?key|openai[_-]?api[_-]?key|secret|token|password|"
            r"passwd|authorization)[\"']?\s*:\s*[\"'])([^\"']{6,})([\"'])"
        ),
        r"\1[REDACTED]\3",
    ),
    (
        re.compile(r"(?im)^([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=(.+)$"),
        r"\1=[REDACTED]",
    ),
    (re.compile(r"sk-[A-Za-z0-9_-]{16,}"), "[REDACTED_OPENAI_KEY]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED_AWS_ACCESS_KEY]"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
)

FABRICATION_PROMPT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:invent|fabricate|make up|hallucinate|create fake|generate fake)\b.*"
            r"\b(?:evidence|citation|citations|pmid|doi|molecule|molecules|assay result|"
            r"assay results)\b",
            re.I | re.S,
        ),
        (
            "Prompt asks Codex to fabricate biomedical evidence, citations, molecules, "
            "or assay results."
        ),
    ),
    (
        re.compile(r"\b(?:fake|placeholder)\s+(?:pmid|doi|citation|assay result)\b", re.I),
        "Prompt asks for fake biomedical references or assay results.",
    ),
    (
        re.compile(
            r"\b(?:fake|placeholder|invent|fabricate|make up)\s+"
            r"(?:registry id|external id|benchling id|assay run|assay result)\b",
            re.I,
        ),
        "Prompt asks for fake external integration identifiers or records.",
    ),
)

PROHIBITED_REQUEST_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsynthesis routes?\b", re.I), "Prompt asks for synthesis routes."),
    (re.compile(r"\blab protocols?\b", re.I), "Prompt asks for lab protocols."),
    (re.compile(r"\b(?:animal|human|patient)\s+dos(?:e|ing)\b", re.I), "Prompt asks for dosing."),
    (re.compile(r"\btreatment advice\b", re.I), "Prompt asks for treatment advice."),
    (
        re.compile(r"\bpatient treatment (?:guidance|instructions?)\b", re.I),
        "Prompt asks for patient treatment guidance.",
    ),
)

BIOMEDICAL_CLAIM_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\b[A-Z][A-Za-z0-9_-]*\s+(?:cures?|treats?|prevents?)\b"),
        "unsupported cure/treatment/prevention claim",
    ),
    (
        re.compile(r"\b[A-Z][A-Za-z0-9_-]*\s+(?:is|are|was|were)\s+(?:safe|efficacious)\b"),
        "unsupported safety or efficacy claim",
    ),
    (
        re.compile(r"\b[A-Z][A-Za-z0-9_-]*\s+(?:is|are|was|were)\s+active\b"),
        "unsupported activity claim",
    ),
    (
        re.compile(r"\b[A-Z][A-Za-z0-9_-]*\s+binds?\s+(?:to\s+)?[A-Za-z0-9_-]+\b"),
        "unsupported binding claim",
    ),
    (
        re.compile(
            r"\bgenerated\s+molecule\b.{0,80}\bdirect\s+"
            r"(?:evidence|experimental evidence)\b",
            re.I | re.S,
        ),
        "generated molecule direct-evidence claim",
    ),
)

ASSAY_RESULT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:IC50|EC50|Ki|Kd|MIC)\s*(?:=|of|:)\s*\d+(?:\.\d+)?\s*(?:nM|uM|µM|mM)\b",
            re.I,
        ),
        "quantitative assay result not present in artifacts",
    ),
    (
        re.compile(
            r"\b(?:positive|negative|active|inactive)\s+(?:assay|result)\b",
            re.I,
        ),
        "assay outcome not present in artifacts",
    ),
)

PROTOCOL_OR_SYNTHESIS_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bsynthesis routes?\b", re.I), "synthesis route"),
    (re.compile(r"\bretrosynthesis\b", re.I), "retrosynthesis instruction"),
    (
        re.compile(
            r"\breagents?\b.{0,80}\b(?:temperature|solvent|yield|stir|heat)\b",
            re.I | re.S,
        ),
        "operational synthesis detail",
    ),
    (re.compile(r"\blab protocols?\b", re.I), "lab protocol"),
    (
        re.compile(
            r"\bstep[- ]by[- ]step\b.{0,80}\b(?:assay|synthesis|protocol)\b",
            re.I | re.S,
        ),
        "step-by-step protocol",
    ),
    (re.compile(r"\b(?:animal|human|patient)\s+dos(?:e|ing)\b", re.I), "dosing instruction"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:mg/kg|mg per kg|mg/day|mg daily)\b", re.I), "dosing amount"),
    (
        re.compile(r"\bpatient treatment (?:guidance|instructions?)\b", re.I),
        "patient treatment guidance",
    ),
    (
        re.compile(r"\bbypass\s+(?:validation|qc|review|guardrails?)\b", re.I),
        "validation bypass instruction",
    ),
)

COPYRIGHT_ARTICLE_PATTERNS = (
    re.compile(r"\b(?:abstract|introduction|methods|results|discussion|conclusion)\b", re.I),
    re.compile(r"\b(?:copyright|all rights reserved|published by|journal)\b", re.I),
)

DEFAULT_FORBIDDEN_COMMANDS = (
    "rm -rf",
    "curl |",
    "curl -",
    "| sh",
    "| bash",
    "sudo",
    "chmod -r 777",
    "printenv",
    "cat .env",
    "git push",
    "gh auth token",
    "openai api key",
    "openai_api_key",
    "git reset --hard",
    "git checkout --",
    "git clean",
    "wget",
    "ssh",
    "scp",
    "aws",
    "gcloud",
)

PMID_PATTERN = re.compile(r"\bPMID:?\s*(\d{4,9})\b", re.I)
DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
CITATION_ID_PATTERN = re.compile(
    r"\b(?:citation|ref|source|cite)[-_ ]?(?:id)?\s*[:#]\s*([A-Za-z0-9_.:-]+)\b",
    re.I,
)
MOLECULE_NAME_PATTERN = re.compile(
    r"\b(?:Generated[-_][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*|GEN[-_][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*)\b"
)
EXTERNAL_ID_PATTERN = re.compile(
    r"\b(?:REG|CMP|BATCH|SAMPLE|BNCH|BENCHLING|ASSAY-RUN|ASSAY-RESULT|"
    r"bfi|bfe|bmr|bms|entity|entry)[-_][A-Za-z0-9][A-Za-z0-9_.-]{2,}\b",
    re.I,
)
MODEL_CODEX_TASK_TYPES = {
    "summarize_model_card",
    "explain_model_metrics",
    "explain_prediction_batch",
    "suggest_feature_debugging",
    "draft_model_limitations",
    "explain_active_design_model_influence",
}
MODEL_REQUIRED_REF_PREFIXES = {
    "model_id": "model_id",
    "dataset_id": "dataset_id",
    "training_run_id": "training_run_id",
    "evaluation_id": "evaluation_id",
    "prediction_batch_artifact_id": "prediction_batch_artifact_id",
}
MODEL_METRIC_KEYS = {
    "accuracy",
    "auc",
    "roc_auc",
    "brier",
    "expected_calibration_error",
    "ece",
    "rmse",
    "mae",
    "r2",
    "precision",
    "recall",
    "f1",
}
MODEL_PREDICTION_KEYS = {
    "prediction_id",
    "candidate_id",
    "candidate_name",
    "endpoint_id",
    "predicted_probability",
    "predicted_value",
    "prediction_label",
    "confidence",
    "uncertainty",
    "applicability_domain",
    "calibration_status",
}
METRIC_VALUE_PATTERN = re.compile(
    r"\b(accuracy|roc_auc|auc|rmse|mae|r2|brier|ece|expected calibration error|"
    r"precision|recall|f1)\b\s*(?:=|:|of|is)?\s*([0-9]+(?:\.[0-9]+)?)",
    re.I,
)
PREDICTION_VALUE_PATTERN = re.compile(
    r"\b(prediction_id|candidate_id|candidate_name|endpoint_id|predicted_probability|"
    r"prediction_label|confidence|uncertainty|applicability_domain|calibration_status)\b"
    r"\s*(?:=|:|is)?\s*([A-Za-z0-9_.:-]+)",
    re.I,
)


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern, replacement in SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def check_prompt(task: CodexTask) -> list[str]:
    warnings: list[str] = []
    prompt = redact_secrets(task.prompt)
    lowered = prompt.lower()
    if any(marker in lowered for marker in [".cache/", "__pycache__", ".pytest_cache", "uv.lock"]):
        warnings.append("Prompt references cache or generated dependency files.")
    if _looks_like_full_article(prompt):
        warnings.append("Prompt may include full copyrighted article text.")
    for pattern, message in [*FABRICATION_PROMPT_PATTERNS, *PROHIBITED_REQUEST_PATTERNS]:
        for match in pattern.finditer(prompt):
            if _is_negated_safety_constraint(prompt, match.start()):
                continue
            warnings.append(message)
            break
    for artifact in task.input_artifact_paths:
        path = Path(artifact)
        if is_secret_path(path):
            warnings.append(f"Secret-like artifact path is not allowed in prompts: {artifact}")
        if _is_cache_path(path):
            warnings.append(f"Artifact path appears to be a cache file: {artifact}")
    return warnings


def check_output(
    result: CodexTaskResult,
    allowed_artifact_refs: set[str],
    allowed_citation_ids: set[str],
) -> CodexTaskResult:
    text = result.output_text or result.stdout
    warnings = [
        *detect_forbidden_biomedical_claims(text),
        *detect_unbacked_citations(text, allowed_citation_ids),
        *detect_protocol_or_synthesis_text(text),
        *_detect_unbacked_assay_results(text, allowed_artifact_refs),
        *_detect_unbacked_molecules(text, allowed_artifact_refs),
        *detect_fake_external_ids(text, allowed_artifact_refs),
        *detect_model_artifact_violations(result, allowed_artifact_refs),
        *detect_structure_artifact_violations(result, allowed_artifact_refs),
    ]
    if not warnings:
        return result
    existing = list(result.guardrail_warnings)
    for warning in warnings:
        if warning not in existing:
            existing.append(warning)
    return result.model_copy(update={"status": "guardrail_failed", "guardrail_warnings": existing})


def detect_forbidden_biomedical_claims(text: str) -> list[str]:
    warnings: list[str] = []
    for pattern, label in BIOMEDICAL_CLAIM_PATTERNS:
        if pattern.search(text):
            warnings.append(f"Forbidden biomedical claim: {label}.")
    return warnings


def detect_unbacked_citations(text: str, allowed_citation_ids: set[str]) -> list[str]:
    allowed = {item.lower() for item in allowed_citation_ids}
    observed = _citation_refs(text)
    warnings = []
    for citation in sorted(observed):
        if citation.lower() not in allowed:
            warnings.append(f"Unbacked citation reference: {citation}.")
    return warnings


def detect_protocol_or_synthesis_text(text: str) -> list[str]:
    warnings: list[str] = []
    for pattern, label in PROTOCOL_OR_SYNTHESIS_PATTERNS:
        if pattern.search(text):
            warnings.append(f"Forbidden protocol/synthesis content: {label}.")
    return warnings


def detect_fake_external_ids(text: str, allowed_artifact_refs: set[str]) -> list[str]:
    allowed = {item.lower() for item in allowed_artifact_refs}
    warnings: list[str] = []
    for match in EXTERNAL_ID_PATTERN.finditer(text):
        external_id = match.group(0)
        lowered = external_id.lower()
        if lowered not in allowed and not any(lowered in item for item in allowed):
            warnings.append(f"Unbacked external record identifier: {external_id}.")
    return warnings


def detect_model_artifact_violations(
    result: CodexTaskResult,
    allowed_artifact_refs: set[str],
) -> list[str]:
    if str(result.task_type) not in MODEL_CODEX_TASK_TYPES:
        return []
    text = result.output_text or result.stdout
    if result.output_json is not None:
        text = text + "\n" + json.dumps(result.output_json, sort_keys=True)
    allowed_lower = {ref.lower() for ref in allowed_artifact_refs}
    warnings = [
        *_detect_missing_model_citations(text, allowed_lower),
        *_detect_fake_model_metrics(text, allowed_lower),
        *_detect_ungrounded_predictions(text, allowed_lower),
        *_detect_forbidden_model_summary_actions(text),
    ]
    return _dedupe(warnings)


def detect_structure_artifact_violations(
    result: CodexTaskResult,
    allowed_artifact_refs: set[str],
) -> list[str]:
    if str(result.task_type) not in STRUCTURE_CODEX_TASK_TYPES:
        return []
    text = result.output_text or result.stdout
    if result.output_json is not None:
        text = text + "\n" + json.dumps(result.output_json, sort_keys=True)
    allowed_lower = {ref.lower() for ref in allowed_artifact_refs}
    warnings = [
        *_detect_missing_structure_citations(text, allowed_lower),
        *_detect_fake_structure_scores(text, allowed_lower),
        *_detect_fake_structure_residue_contacts(text, allowed_lower),
        *_detect_structure_overclaims(text),
    ]
    return _dedupe(warnings)


def is_secret_path(path: Path) -> bool:
    parts = [part.lower() for part in path.parts]
    for part in parts:
        if part in SECRET_FILE_MARKERS:
            return True
        if part.endswith((".pem", ".p12", ".pfx")):
            return True
        if part in {"credentials.json", "secrets.json"}:
            return True
    return False


def task_guardrail_warnings(task: CodexTask, config: CodexBackboneConfig) -> list[str]:
    warnings = check_prompt(task)
    forbidden = [
        *DEFAULT_FORBIDDEN_COMMANDS,
        *config.codex_forbidden_commands,
        *task.forbidden_commands,
    ]
    prompt_lower = task.prompt.lower()
    for command in forbidden:
        if command and command.lower() in prompt_lower:
            warnings.append(f"Prompt references forbidden command: {command}")
    shell_commands_requested = bool(task.allowed_commands or config.codex_allowed_commands)
    if not config.codex_allow_shell_commands and shell_commands_requested:
        warnings.append("Shell command execution is disabled for this Codex task.")
    return _dedupe(warnings)


def output_guardrail_warnings(
    text: str,
    *,
    task_type: str = "",
    allowed_artifact_refs: set[str] | None = None,
) -> list[str]:
    result = CodexTaskResult(
        task_id="output-guardrail-check",
        task_type=task_type or "summarize_run",  # type: ignore[arg-type]
        status="succeeded",
        output_text=text,
    )
    return _dedupe(
        [
            *detect_forbidden_biomedical_claims(text),
            *detect_protocol_or_synthesis_text(text),
            *_detect_unbacked_assay_results(text, set()),
            *detect_model_artifact_violations(result, allowed_artifact_refs or set()),
            *detect_structure_artifact_violations(result, allowed_artifact_refs or set()),
        ]
    )


def has_blocking_task_guardrail(warnings: list[str]) -> bool:
    return any(_is_reject_warning(warning) for warning in warnings)


def collect_allowed_refs_from_artifacts(paths: list[str]) -> tuple[set[str], set[str]]:
    artifact_refs: set[str] = set()
    citation_ids: set[str] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if is_secret_path(path) or not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        artifact_refs.update(_artifact_refs(text, path))
        citation_ids.update(_citation_refs(text))
        citation_ids.update(_structured_citation_refs(text))
    return artifact_refs, citation_ids


def _detect_unbacked_assay_results(text: str, allowed_artifact_refs: set[str]) -> list[str]:
    allowed_text = " ".join(allowed_artifact_refs).lower()
    warnings: list[str] = []
    for pattern, label in ASSAY_RESULT_PATTERNS:
        match = pattern.search(text)
        if match and match.group(0).lower() not in allowed_text:
            warnings.append(f"Unbacked assay result: {label}.")
    return warnings


def _detect_unbacked_molecules(text: str, allowed_artifact_refs: set[str]) -> list[str]:
    allowed = {item.lower() for item in allowed_artifact_refs}
    warnings: list[str] = []
    for match in MOLECULE_NAME_PATTERN.finditer(text):
        name = match.group(0)
        if name.lower() not in allowed:
            warnings.append(f"Unbacked generated molecule reference: {name}.")
    return warnings


def _detect_missing_model_citations(text: str, allowed_lower: set[str]) -> list[str]:
    lowered_text = text.lower()
    warnings: list[str] = []
    for prefix, label in MODEL_REQUIRED_REF_PREFIXES.items():
        values = _prefixed_allowed_values(allowed_lower, prefix)
        if not values:
            warnings.append(f"Model Codex summary missing source artifact field: {label}.")
            continue
        if not any(value in lowered_text for value in values):
            warnings.append(f"Model Codex summary did not cite required {label}.")
    return warnings


def _detect_fake_model_metrics(text: str, allowed_lower: set[str]) -> list[str]:
    warnings: list[str] = []
    for match in METRIC_VALUE_PATTERN.finditer(text):
        key = _normalize_model_key(match.group(1))
        raw_value = _normalize_numeric_string(match.group(2))
        candidates = {
            f"metric:{key}:{raw_value}",
            f"{key}:{raw_value}",
            f"{key}={raw_value}",
        }
        if candidates.isdisjoint(allowed_lower):
            warnings.append(f"Unbacked model metric: {key}={raw_value}.")
    return warnings


def _detect_ungrounded_predictions(text: str, allowed_lower: set[str]) -> list[str]:
    warnings: list[str] = []
    for match in PREDICTION_VALUE_PATTERN.finditer(text):
        key = _normalize_model_key(match.group(1))
        raw_value = match.group(2).strip(" .,:;\"'").lower()
        if not raw_value:
            continue
        if key in {"confidence", "uncertainty", "predicted_probability"}:
            raw_value = _normalize_numeric_string(raw_value)
        candidates = {
            f"prediction:{key}:{raw_value}",
            f"{key}:{raw_value}",
            raw_value,
        }
        if candidates.isdisjoint(allowed_lower):
            warnings.append(f"Ungrounded model prediction field: {key}={raw_value}.")
    return warnings


def _detect_forbidden_model_summary_actions(text: str) -> list[str]:
    patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (re.compile(r"\bEvidenceItem\b|\bevidence_item\b", re.I), "create EvidenceItem"),
        (re.compile(r"\bAssayResult\b|\bassay result\b", re.I), "create or claim assay result"),
        (re.compile(r"\bapprove[sd]?\s+(?:the\s+)?model\b", re.I), "approve model"),
        (
            re.compile(r"\b(?:changed|updated|modified)\s+(?:the\s+)?model card\b", re.I),
            "change model card",
        ),
        (
            re.compile(r"\bclinical use\b|\bclinical recommendation\b", re.I),
            "recommend clinical use",
        ),
    )
    warnings: list[str] = []
    for pattern, label in patterns:
        match = pattern.search(text)
        if match and not _is_negated_safety_constraint(text, match.start()):
            warnings.append(f"Forbidden model Codex action: {label}.")
    return warnings


def _detect_missing_structure_citations(text: str, allowed_lower: set[str]) -> list[str]:
    lowered_text = text.lower()
    warnings: list[str] = []
    for prefix, label in STRUCTURE_REQUIRED_REF_PREFIXES.items():
        values = _prefixed_allowed_values(allowed_lower, prefix)
        if not values:
            warnings.append(f"Structure Codex summary missing source artifact field: {label}.")
            continue
        if not any(value in lowered_text for value in values):
            warnings.append(f"Structure Codex summary did not cite required {label}.")
    return warnings


def _detect_fake_structure_scores(text: str, allowed_lower: set[str]) -> list[str]:
    warnings: list[str] = []
    for match in STRUCTURE_SCORE_PATTERN.finditer(text):
        raw_value = _normalize_numeric_string(match.group(1))
        candidates = {
            f"structure_score:docking_score:{raw_value}",
            f"docking_score:{raw_value}",
            f"docking_score={raw_value}",
        }
        if candidates.isdisjoint(allowed_lower):
            warnings.append(f"Unbacked structure docking score: {raw_value}.")
    return warnings


def _detect_fake_structure_residue_contacts(text: str, allowed_lower: set[str]) -> list[str]:
    warnings: list[str] = []
    allowed_contacts = _prefixed_allowed_values(allowed_lower, "structure_residue")
    for match in STRUCTURE_RESIDUE_PATTERN.finditer(text):
        residue = match.group(0)
        if residue.lower() not in allowed_contacts:
            warnings.append(f"Unbacked structure residue contact: {residue}.")
    return warnings


def _detect_structure_overclaims(text: str) -> list[str]:
    warnings: list[str] = []
    for pattern, label in STRUCTURE_OVERCLAIM_PATTERNS:
        match = pattern.search(text)
        if (
            match
            and "do not" not in match.group(0).lower()
            and not _is_negated_safety_constraint(text, match.start())
        ):
            warnings.append(f"Forbidden structure Codex claim: {label}.")
    return warnings


def _prefixed_allowed_values(allowed_lower: set[str], prefix: str) -> set[str]:
    marker = f"{prefix}:"
    return {
        item.removeprefix(marker)
        for item in allowed_lower
        if item.startswith(marker) and item.removeprefix(marker)
    }


def _citation_refs(text: str) -> set[str]:
    refs: set[str] = set()
    refs.update(f"PMID:{match.group(1)}" for match in PMID_PATTERN.finditer(text))
    refs.update(match.group(0) for match in DOI_PATTERN.finditer(text))
    for match in CITATION_ID_PATTERN.finditer(text):
        value = match.group(1).strip(" .,:;)")
        if value and value.lower() not in {"id", "ids"}:
            refs.add(value)
    return refs


def _artifact_refs(text: str, path: Path) -> set[str]:
    refs: set[str] = {path.name, str(path.resolve())}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if payload is not None:
        _collect_json_refs(payload, refs)
    else:
        refs.update(_citation_refs(text))
        refs.update(match.group(0) for match in MOLECULE_NAME_PATTERN.finditer(text))
        for pattern, _label in ASSAY_RESULT_PATTERNS:
            refs.update(match.group(0) for match in pattern.finditer(text))
    return refs


def _collect_json_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        for key, raw in value.items():
            lowered = str(key).lower()
            if lowered in {
                "pmid",
                "doi",
                "source_record_id",
                "external_record_id",
                "external_record_ref",
                "external_system_id",
                "registry_id",
                "benchling_id",
                "assay_run_id",
                "assay_result_id",
                "sync_job_id",
                "sync_record_id",
                "citation_id",
                "result_id",
                "molecule_id",
                "generated_id",
                "candidate_name",
                "name",
                "model_id",
                "dataset_id",
                "training_dataset_id",
                "training_run_id",
                "evaluation_id",
                "prediction_id",
                "batch_id",
                "prediction_batch_artifact_id",
                "artifact_id",
                "structure_id",
                "selection_id",
                "receptor_prep_id",
                "docking_run_id",
                "pose_id",
                "interaction_profile_id",
                "endpoint_id",
                "candidate_id",
                "prediction_label",
                "applicability_domain",
                "calibration_status",
            } and raw is not None:
                normalized = _normalize_ref(lowered, str(raw))
                refs.add(normalized)
                refs.add(str(raw))
                refs.add(f"{_model_ref_prefix(lowered)}:{str(raw)}")
                refs.add(f"{_structure_ref_prefix(lowered)}:{str(raw)}")
            if lowered == "artifact_ids" and isinstance(raw, list):
                for item in raw:
                    if item not in (None, ""):
                        refs.add(str(item))
                        refs.add(f"artifact_id:{str(item)}")
            if lowered in {"key_residue_contacts", "residues"} and isinstance(raw, list):
                for item in raw:
                    if item not in (None, ""):
                        refs.add(str(item))
                        refs.add(f"structure_residue:{str(item)}")
            if lowered in {"residue", "contact_residue"} and raw not in (None, ""):
                refs.add(str(raw))
                refs.add(f"structure_residue:{str(raw)}")
            if lowered in {"docking_score", "structure_score"} and raw is not None:
                normalized_value = _normalize_numeric_string(str(raw))
                refs.add(f"structure_score:{lowered}:{normalized_value}")
                refs.add(f"{lowered}:{normalized_value}")
                refs.add(f"{lowered}={normalized_value}")
            if lowered in MODEL_METRIC_KEYS and raw is not None:
                normalized_key = _normalize_model_key(lowered)
                normalized_value = _normalize_numeric_string(str(raw))
                refs.add(f"metric:{normalized_key}:{normalized_value}")
                refs.add(f"{normalized_key}:{normalized_value}")
                refs.add(f"{normalized_key}={normalized_value}")
            if lowered in MODEL_PREDICTION_KEYS and raw is not None:
                normalized_key = _normalize_model_key(lowered)
                normalized_value = str(raw).lower()
                if normalized_key in {"confidence", "uncertainty", "predicted_probability"}:
                    normalized_value = _normalize_numeric_string(normalized_value)
                refs.add(f"prediction:{normalized_key}:{normalized_value}")
                refs.add(f"{normalized_key}:{normalized_value}")
            _collect_json_refs(raw, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_json_refs(item, refs)
    elif isinstance(value, str):
        refs.update(_citation_refs(value))
        refs.update(match.group(0) for match in MOLECULE_NAME_PATTERN.finditer(value))
        for pattern, _label in ASSAY_RESULT_PATTERNS:
            refs.update(match.group(0) for match in pattern.finditer(value))


def _structured_citation_refs(text: str) -> set[str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return set()
    refs: set[str] = set()
    _collect_structured_citation_refs(payload, refs)
    return refs


def _collect_structured_citation_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        for key, raw in value.items():
            lowered = str(key).lower()
            if lowered == "pmid" and raw not in (None, "", []):
                refs.add(str(raw))
                refs.add(_normalize_ref(lowered, str(raw)))
            elif lowered in {"doi", "citation_id", "source_record_id"} and raw not in (
                None,
                "",
                [],
            ):
                refs.add(str(raw))
            _collect_structured_citation_refs(raw, refs)
    elif isinstance(value, list):
        for item in value:
            _collect_structured_citation_refs(item, refs)


def _normalize_ref(key: str, value: str) -> str:
    if key == "pmid" and not value.upper().startswith("PMID"):
        return f"PMID:{value}"
    return value


def _model_ref_prefix(key: str) -> str:
    if key == "training_dataset_id":
        return "dataset_id"
    if key == "batch_id":
        return "prediction_batch_artifact_id"
    return key


def _structure_ref_prefix(key: str) -> str:
    if key == "artifact_id":
        return "artifact_id"
    return key


def _normalize_model_key(key: str) -> str:
    return key.lower().replace(" ", "_")


def _normalize_numeric_string(value: str) -> str:
    stripped = value.strip().rstrip(".,;")
    try:
        number = float(stripped)
    except ValueError:
        return stripped.lower()
    return f"{number:g}"


def _looks_like_full_article(text: str) -> bool:
    if len(text.split()) < 1200:
        return False
    matches = sum(1 for pattern in COPYRIGHT_ARTICLE_PATTERNS if pattern.search(text))
    return matches >= 2


def _is_cache_path(path: Path) -> bool:
    lowered = str(path).lower()
    return any(marker in lowered for marker in [".cache", "__pycache__", ".pytest_cache"])


def _is_negated_safety_constraint(text: str, match_start: int) -> bool:
    prefix = text[max(0, match_start - 160) : match_start].lower()
    return any(
        marker in prefix
        for marker in (
            "do not ",
            "don't ",
            "no ",
            "never ",
            "must not ",
            "cannot ",
            "can not ",
            "not allowed to ",
            "without ",
        )
    )


def _is_reject_warning(warning: str) -> bool:
    lowered = warning.lower()
    return any(
        marker in lowered
        for marker in [
            "fabricate",
            "fake biomedical",
            "synthesis",
            "protocol",
            "dosing",
            "treatment",
            "secret-like",
            "forbidden command",
        ]
    )


def _dedupe(values: list[str]) -> list[str]:
    deduped: list[str] = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return deduped
