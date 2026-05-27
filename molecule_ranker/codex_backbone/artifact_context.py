from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from molecule_ranker.codex_backbone.guardrails import (
    detect_unbacked_citations,
    is_secret_path,
    redact_secrets,
)
from molecule_ranker.codex_backbone.schemas import CodexTaskResult

COMMON_ARTIFACT_NAMES = (
    "report.md",
    "candidates.json",
    "trace.json",
)

TASK_ARTIFACT_NAMES: dict[str, tuple[str, ...]] = {
    "summarize_run": (
        *COMMON_ARTIFACT_NAMES,
        "generated_candidates.json",
        "generated_molecules.json",
        "developability.json",
        "developability_assessments.json",
        "experimental_evidence.json",
        "experimental_results.json",
    ),
    "explain_ranking": (
        *COMMON_ARTIFACT_NAMES,
        "generated_candidates.json",
        "generated_molecules.json",
        "developability.json",
        "developability_assessments.json",
        "experimental_evidence.json",
    ),
    "compare_candidates": (
        *COMMON_ARTIFACT_NAMES,
        "generated_candidates.json",
        "generated_molecules.json",
        "developability.json",
        "experimental_evidence.json",
    ),
    "compare_runs": (
        *COMMON_ARTIFACT_NAMES,
        "generated_candidates.json",
        "developability.json",
        "experimental_evidence.json",
    ),
    "draft_dossier": (
        *COMMON_ARTIFACT_NAMES,
        "review_queue.json",
        "developability.json",
        "experimental_evidence.json",
    ),
    "generate_review_questions": (
        *COMMON_ARTIFACT_NAMES,
        "review_queue.json",
        "developability.json",
        "experimental_evidence.json",
    ),
    "explain_active_learning": (
        "active_learning_batch.json",
        "candidates.json",
        "trace.json",
        "experimental_evidence.json",
    ),
    "plan_followup_run": (
        *COMMON_ARTIFACT_NAMES,
        "active_learning_batch.json",
        "developability.json",
        "experimental_evidence.json",
        "experimental_results.json",
        "review_queue.json",
    ),
    "inspect_artifacts": (
        *COMMON_ARTIFACT_NAMES,
        "generated_candidates.json",
        "generated_molecules.json",
        "developability.json",
        "experimental_evidence.json",
        "active_learning_batch.json",
        "review_queue.json",
    ),
}

FULL_TEXT_NAME_MARKERS = (
    "full_text",
    "full-text",
    "article_text",
    "article-full",
    "pmc_full",
    "pdf_text",
)

CACHE_PATH_MARKERS = (
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)

CITATION_ID_KEYS = {
    "citation_id",
    "citationid",
    "source_id",
    "paper_id",
    "query_id",
    "reference_id",
    "ref_id",
}
CANDIDATE_CONTAINER_KEYS = {
    "candidates",
    "generated_candidates",
    "generated_molecule_hypotheses",
    "generated_molecules",
    "retained_generated",
}
CANDIDATE_ID_KEYS = {
    "name",
    "candidate_id",
    "candidateid",
    "molecule_id",
    "moleculeid",
    "generated_id",
    "generatedid",
}

PMID_FIELD_PATTERN = re.compile(r"\bPMID:?\s*(\d{4,9})\b", re.I)
DOI_FIELD_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.I)
PMCID_FIELD_PATTERN = re.compile(r"\bPMC\d+\b", re.I)
CITATION_REF_PATTERN = re.compile(
    r"\b(?:citation|ref|source|cite)[-_ ]?(?:id)?\s*[:#]\s*([A-Za-z0-9_.:-]+)\b",
    re.I,
)
CANDIDATE_REF_PATTERN = re.compile(
    r"\b(?:candidate|molecule|generated[-_ ]?id|candidate[-_ ]?id)"
    r"(?:[-_ ]?name)?\s*[:#]\s*([A-Za-z0-9][A-Za-z0-9_.:+ -]{0,80})",
    re.I,
)
ARTICLE_SECTION_PATTERN = re.compile(
    r"\b(?:abstract|introduction|methods|results|discussion|references)\b",
    re.I,
)


class CodexArtifactSnippet(BaseModel):
    path: str
    artifact_type: str
    size_bytes: int
    content: str
    truncated: bool = False
    excluded: bool = False
    warnings: list[str] = Field(default_factory=list)


class CodexArtifactContext(BaseModel):
    run_dir: str
    max_bytes: int
    selected_artifact_paths: list[str]
    artifacts: list[CodexArtifactSnippet]
    allowed_citation_ids: set[str] = Field(default_factory=set)
    allowed_candidate_ids: set[str] = Field(default_factory=set)
    warnings: list[str] = Field(default_factory=list)


def build_artifact_context(run_dir: Path, max_bytes: int) -> CodexArtifactContext:
    selected_paths = select_relevant_artifacts("summarize_run", run_dir)
    snippets: list[CodexArtifactSnippet] = []
    warnings: list[str] = []
    for path in selected_paths:
        summary = summarize_large_artifact(path, max_bytes)
        excluded = summary.startswith("[EXCLUDED:")
        truncated = summary.startswith("[TRUNCATED:")
        artifact_warnings: list[str] = []
        if excluded:
            artifact_warnings.append(summary.split("]", maxsplit=1)[0].lstrip("["))
            warnings.extend(artifact_warnings)
        elif truncated:
            artifact_warnings.append(summary.split("]", maxsplit=1)[0].lstrip("["))
        snippets.append(
            CodexArtifactSnippet(
                path=str(path.resolve()),
                artifact_type=_artifact_type(path),
                size_bytes=path.stat().st_size,
                content=summary,
                truncated=truncated,
                excluded=excluded,
                warnings=artifact_warnings,
            )
        )
    included_paths = [Path(snippet.path) for snippet in snippets if not snippet.excluded]
    return CodexArtifactContext(
        run_dir=str(run_dir.resolve()),
        max_bytes=max_bytes,
        selected_artifact_paths=[str(path.resolve()) for path in included_paths],
        artifacts=snippets,
        allowed_citation_ids=extract_allowed_citation_ids(included_paths),
        allowed_candidate_ids=extract_allowed_candidate_ids(included_paths),
        warnings=_dedupe(warnings),
    )


def select_relevant_artifacts(task_type: str, run_dir: Path) -> list[Path]:
    names = TASK_ARTIFACT_NAMES.get(task_type, TASK_ARTIFACT_NAMES["inspect_artifacts"])
    selected: list[Path] = []
    seen: set[Path] = set()
    for name in names:
        path = (run_dir / name).resolve()
        if path in seen:
            continue
        seen.add(path)
        if not path.exists() or not path.is_file():
            continue
        if _is_excluded_path(path):
            continue
        selected.append(path)
    return selected


def extract_allowed_citation_ids(artifacts: Iterable[Path]) -> set[str]:
    allowed: set[str] = set()
    for path in artifacts:
        if _is_excluded_path(path):
            continue
        text = _read_text(path)
        if path.suffix.lower() == ".json":
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                payload = None
            if payload is not None:
                _collect_citations_from_json(payload, allowed)
        _collect_citations_from_text(text, allowed)
    return allowed


def extract_allowed_candidate_ids(artifacts: Iterable[Path]) -> set[str]:
    allowed: set[str] = set()
    for path in artifacts:
        if _is_excluded_path(path) or path.suffix.lower() != ".json":
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        _collect_candidate_ids(payload, allowed, in_candidate_context=False)
    return allowed


def summarize_large_artifact(path: Path, max_bytes: int) -> str:
    if _is_excluded_path(path):
        return "[EXCLUDED: cache, secret, credential, or full-text article artifact]"
    data = path.read_bytes()
    text_for_article_check = data[: min(len(data), max(max_bytes, 8192))].decode(
        errors="replace"
    )
    if _looks_like_full_article(path, text_for_article_check):
        return "[EXCLUDED: full article text is not included; use citation metadata only]"
    truncated = len(data) > max_bytes
    text = data[:max_bytes].decode(errors="replace") if truncated else data.decode(errors="replace")
    text = redact_secrets(text)
    if truncated:
        return (
            f"[TRUNCATED: original_size_bytes={len(data)}; "
            f"included_bytes={max_bytes}; content redacted]\n{text}"
        )
    return text


def validate_output_references(
    result: CodexTaskResult,
    context: CodexArtifactContext,
) -> list[str]:
    text = result.output_text or result.stdout
    if result.output_json is not None:
        text = f"{text}\n{json.dumps(result.output_json, sort_keys=True)}"
    warnings = detect_unbacked_citations(text, context.allowed_citation_ids)
    warnings.extend(_detect_unbacked_candidate_refs(text, context.allowed_candidate_ids))
    return _dedupe(warnings)


def _is_excluded_path(path: Path) -> bool:
    lowered = str(path).lower()
    if any(marker in lowered for marker in CACHE_PATH_MARKERS):
        return True
    if is_secret_path(path):
        return True
    if any(marker in path.name.lower() for marker in FULL_TEXT_NAME_MARKERS):
        return True
    return False


def _looks_like_full_article(path: Path, text: str) -> bool:
    lowered_name = path.name.lower()
    if any(marker in lowered_name for marker in FULL_TEXT_NAME_MARKERS):
        return True
    if len(text.split()) < 800:
        return False
    section_hits = len({match.group(0).lower() for match in ARTICLE_SECTION_PATTERN.finditer(text)})
    article_marker = re.search(r"\b(?:journal|copyright|references)\b", text, re.I)
    return section_hits >= 4 and bool(article_marker)


def _artifact_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".json":
        return "json"
    if suffix in {".md", ".markdown"}:
        return "markdown"
    return "artifact"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _collect_citations_from_json(value: Any, allowed: set[str]) -> None:
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key).lower()
            if raw_value is None:
                continue
            if key == "pmid":
                _add_pmid(raw_value, allowed)
            elif key == "doi":
                allowed.add(str(raw_value))
            elif key == "pmcid":
                allowed.add(str(raw_value))
            elif key in CITATION_ID_KEYS:
                allowed.add(str(raw_value))
            elif key == "citation" and isinstance(raw_value, dict):
                _collect_citations_from_json(raw_value, allowed)
                continue
            _collect_citations_from_json(raw_value, allowed)
    elif isinstance(value, list):
        for item in value:
            _collect_citations_from_json(item, allowed)
    elif isinstance(value, str):
        _collect_citations_from_text(value, allowed)


def _collect_citations_from_text(text: str, allowed: set[str]) -> None:
    for match in PMID_FIELD_PATTERN.finditer(text):
        _add_pmid(match.group(1), allowed)
    for match in DOI_FIELD_PATTERN.finditer(text):
        allowed.add(match.group(0))
    for match in PMCID_FIELD_PATTERN.finditer(text):
        allowed.add(match.group(0))
    for match in CITATION_REF_PATTERN.finditer(text):
        allowed.add(match.group(1))


def _add_pmid(value: Any, allowed: set[str]) -> None:
    raw = str(value).strip()
    if not raw:
        return
    digits_match = re.search(r"\d{4,9}", raw)
    if digits_match:
        allowed.add(f"PMID:{digits_match.group(0)}")
    allowed.add(raw)


def _collect_candidate_ids(
    value: Any,
    allowed: set[str],
    *,
    in_candidate_context: bool,
) -> None:
    if isinstance(value, dict):
        for raw_key, raw_value in value.items():
            key = str(raw_key).lower()
            nested_candidate_context = in_candidate_context or key in CANDIDATE_CONTAINER_KEYS
            if in_candidate_context and key in CANDIDATE_ID_KEYS and raw_value is not None:
                allowed.add(str(raw_value))
            _collect_candidate_ids(
                raw_value,
                allowed,
                in_candidate_context=nested_candidate_context,
            )
    elif isinstance(value, list):
        for item in value:
            _collect_candidate_ids(item, allowed, in_candidate_context=in_candidate_context)


def _detect_unbacked_candidate_refs(text: str, allowed_candidate_ids: set[str]) -> list[str]:
    allowed = {item.lower() for item in allowed_candidate_ids if item}
    warnings: list[str] = []
    for match in CANDIDATE_REF_PATTERN.finditer(text):
        candidate = match.group(1).strip().strip(".,;")
        if candidate and candidate.lower() not in allowed:
            warnings.append(f"Unbacked candidate reference: {candidate}.")
    return warnings


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped
