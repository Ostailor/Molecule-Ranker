from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from molecule_ranker.codex_backbone.schemas import CodexBackboneConfig, CodexTask, CodexTaskResult

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
        re.compile(r"\b[A-Z][A-Za-z0-9_-]*\s+(?:cures?|treats?|prevents?)\b", re.I),
        "unsupported cure/treatment/prevention claim",
    ),
    (
        re.compile(r"\b[A-Z][A-Za-z0-9_-]*\s+(?:is|are|was|were)\s+(?:safe|efficacious)\b", re.I),
        "unsupported safety or efficacy claim",
    ),
    (
        re.compile(r"\b[A-Z][A-Za-z0-9_-]*\s+(?:is|are|was|were)\s+active\b", re.I),
        "unsupported activity claim",
    ),
    (
        re.compile(r"\b[A-Z][A-Za-z0-9_-]*\s+binds?\s+(?:to\s+)?[A-Za-z0-9_-]+\b", re.I),
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


def output_guardrail_warnings(text: str) -> list[str]:
    return _dedupe(
        [
            *detect_forbidden_biomedical_claims(text),
            *detect_protocol_or_synthesis_text(text),
            *_detect_unbacked_assay_results(text, set()),
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
                "citation_id",
                "result_id",
                "molecule_id",
                "generated_id",
                "candidate_name",
                "name",
            } and raw is not None:
                normalized = _normalize_ref(lowered, str(raw))
                refs.add(normalized)
                refs.add(str(raw))
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
