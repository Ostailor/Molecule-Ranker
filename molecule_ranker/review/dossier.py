from __future__ import annotations

import json
import re
from typing import Any

from molecule_ranker.experiments.guardrails import (
    sanitize_experimental_output_text,
    should_omit_experimental_output_key,
)
from molecule_ranker.review.schemas import REVIEW_LIMITATIONS, CandidateDossier, ReviewWorkspace

GENERATED_DIRECT_EVIDENCE_NOTICE = "Generated molecules have no direct experimental evidence."
EXISTING_ORIGIN_NOTICE = (
    "This existing-molecule dossier does not infer suitability for the queried disease "
    "from any approval, prior use, or database presence."
)

_CITATION_KEYS = {
    "authors",
    "doi",
    "journal",
    "openalex_id",
    "pmcid",
    "pmid",
    "publication_year",
    "title",
    "url",
    "year",
}
_OMITTED_TEXT_KEYS = {
    "abstract",
    "article_text",
    "body",
    "full_text",
    "methods",
    "paper_text",
    "text",
}
_PROCEDURAL_TERMS = (
    "dosage",
    "dose",
    "lab protocol",
    "protocol",
    "reaction condition",
    "reagent",
    "route",
    "synthesis",
    "temperature",
    "treatment advice",
)


class DossierWriterAgent:
    def build_dossier(self, workspace: ReviewWorkspace, review_item_id: str) -> CandidateDossier:
        item = _get_item(workspace, review_item_id)
        decisions = [
            decision
            for decision in workspace.decisions
            if decision.review_item_id == review_item_id
        ]
        comments = [
            comment
            for comment in workspace.comments
            if comment.review_item_id == review_item_id
        ]
        followups = [
            request
            for request in workspace.followup_requests
            if request.review_item_id == review_item_id
        ]
        sections = _build_sections(
            workspace=workspace,
            item=item,
            decisions=decisions,
            comments=comments,
            followups=followups,
        )
        return CandidateDossier(
            review_item_id=review_item_id,
            disease_name=item.disease_name,
            candidate_name=item.candidate_name,
            candidate_origin=item.candidate_origin,
            executive_summary=str(sections[0]["content"]["summary"]),
            evidence_sections=[
                section
                for section in sections
                if section["title"]
                in {
                    "Candidate identity",
                    "Origin",
                    "Disease and target rationale",
                    "Molecule-target evidence",
                    "Experimental evidence",
                    "Literature evidence",
                    "Generated molecule provenance",
                    "Source provenance and artifact paths",
                }
            ],
            risk_sections=[
                section
                for section in sections
                if section["title"]
                in {
                    "Safety and warning evidence",
                    "Developability assessment",
                    "Key uncertainties",
                    "Recommended follow-up questions",
                }
            ],
            reviewer_decisions=decisions,
            reviewer_comments=comments,
            limitations=list(REVIEW_LIMITATIONS),
            metadata={
                "workspace_id": workspace.workspace_id,
                "sections": sections,
                "source_artifact_paths": _artifact_paths(workspace, item),
            },
        )


def render_dossier_markdown(dossier: CandidateDossier) -> str:
    sections = dossier.metadata.get("sections")
    if not isinstance(sections, list):
        sections = _legacy_sections(dossier)

    lines = [f"# Candidate Dossier: {dossier.candidate_name}", ""]
    for section in sections:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "Section")
        content = section.get("content", {})
        lines.extend([f"## {title}", ""])
        if title == "Molecule-target evidence":
            lines.extend(["### Evidence summary", ""])
        if title == "Reviewer decisions and comments":
            lines.extend(["### Reviewer Decisions", ""])
        lines.extend(_render_content_markdown(content))
        if title == "Reviewer decisions and comments":
            lines.extend(["", "### Reviewer Comments"])
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_dossier_json(dossier: CandidateDossier) -> str:
    return json.dumps(dossier.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def _build_sections(
    *,
    workspace: ReviewWorkspace,
    item: Any,
    decisions: list[Any],
    comments: list[Any],
    followups: list[Any],
) -> list[dict[str, Any]]:
    return [
        {
            "title": "Executive summary",
            "content": {
                "summary": _executive_summary(item),
            },
        },
        {
            "title": "Candidate identity",
            "content": {
                "candidate_id": item.candidate_id,
                "candidate_name": item.candidate_name,
                "canonical_smiles": item.canonical_smiles,
                "target_symbols": item.target_symbols,
                "model_score": item.score,
                "model_confidence": item.confidence,
                "priority_bucket": item.priority_bucket,
                "review_status": item.review_status,
            },
        },
        {
            "title": "Origin",
            "content": _origin_content(item),
        },
        {
            "title": "Disease and target rationale",
            "content": {
                "disease_name": item.disease_name,
                "target_symbols": item.target_symbols,
                "target_evidence_count": item.evidence_summary.get("target_evidence_count"),
                "rationale_note": (
                    "Disease and target rationale is a review context assembled from "
                    "computational and public-source evidence."
                ),
            },
        },
        {
            "title": "Molecule-target evidence",
            "content": {
                "molecule_evidence_count": item.evidence_summary.get(
                    "molecule_evidence_count"
                ),
                "score_breakdown": _safe_payload(
                    item.evidence_summary.get("score_breakdown")
                ),
                "evidence_records": _safe_payload(item.evidence_summary.get("items", [])),
            },
        },
        {
            "title": "Experimental evidence",
            "content": _experimental_evidence_content(item),
        },
        {
            "title": "Literature evidence",
            "content": {
                "claim_counts": item.evidence_summary.get("literature_claim_counts")
                or item.literature_summary.get("claim_counts"),
                "quality_score": item.literature_summary.get("quality_score"),
                "citations": _citation_metadata(item.literature_summary),
                "note": "Citation metadata is included; article text is not copied.",
            },
        },
        {
            "title": "Safety and warning evidence",
            "content": {
                "warning_count": len(item.warnings),
                "safety_warning_count": item.evidence_summary.get("safety_warning_count"),
                "warnings": _safe_payload(item.warnings),
                "risk_flags": _safe_payload(item.risk_flags),
            },
        },
        {
            "title": "Developability assessment",
            "content": _safe_payload(item.developability_summary),
        },
        {
            "title": "Generated molecule provenance",
            "content": _generated_content(item),
        },
        {
            "title": "Key uncertainties",
            "content": {
                "uncertainties": _uncertainties(item),
            },
        },
        {
            "title": "Reviewer decisions and comments",
            "content": {
                "decisions": [decision.model_dump(mode="json") for decision in decisions],
                "comments": [comment.model_dump(mode="json") for comment in comments],
            },
        },
        {
            "title": "Recommended follow-up questions",
            "content": {
                "requested_followups": [
                    request.model_dump(mode="json") for request in followups
                ],
                "default_questions": _default_followup_questions(item),
            },
        },
        {
            "title": "Limitations and disclaimers",
            "content": {
                "limitations": _limitations_for_item(item),
            },
        },
        {
            "title": "Source provenance and artifact paths",
            "content": {
                "workspace_id": workspace.workspace_id,
                "run_id": workspace.run_id,
                "artifact_paths": _artifact_paths(workspace, item),
                "source_note": (
                    "Paths and source identifiers are provenance metadata for local review."
                ),
            },
        },
    ]


def _executive_summary(item: Any) -> str:
    if item.candidate_origin == "generated":
        return (
            f"Expert triage dossier for generated hypothesis {item.candidate_name}. "
            f"{GENERATED_DIRECT_EVIDENCE_NOTICE} This packet supports review only and "
            "is not a clinical conclusion."
        )
    return (
        f"Expert triage dossier for existing evidence-backed molecule {item.candidate_name}. "
        f"{EXISTING_ORIGIN_NOTICE} This is not a clinical conclusion."
    )


def _origin_content(item: Any) -> dict[str, Any]:
    if item.candidate_origin == "generated":
        return {
            "origin_label": "generated hypothesis",
            "notice": GENERATED_DIRECT_EVIDENCE_NOTICE,
            "interpretation_boundary": (
                "Generation provenance is not experimental confirmation."
            ),
        }
    return {
        "origin_label": "existing evidence-backed molecule",
        "notice": EXISTING_ORIGIN_NOTICE,
    }


def _experimental_evidence_content(item: Any) -> dict[str, Any]:
    summary = item.evidence_summary.get("experimental_results")
    if not isinstance(summary, dict):
        return {
            "result_count": 0,
            "note": "No linked imported experimental results are recorded for this review item.",
            "boundary_note": (
                "Reviewer decisions remain separate from imported experimental evidence."
            ),
        }
    return {
        "result_count": summary.get("result_count", 0),
        "positive_count": summary.get("positive_count", 0),
        "negative_count": summary.get("negative_count", 0),
        "inconclusive_count": summary.get("inconclusive_count", 0),
        "failed_qc_count": summary.get("failed_qc_count", 0),
        "safety_concern_count": summary.get("safety_concern_count", 0),
        "results": _safe_payload(summary.get("results", [])),
        "review_suggestion": _safe_payload(
            item.metadata.get("experimental_review_suggestion", {})
        ),
        "boundary_note": summary.get(
            "boundary_note",
            "Reviewer decisions remain separate from imported experimental evidence.",
        ),
    }


def _generated_content(item: Any) -> dict[str, Any]:
    if item.candidate_origin != "generated":
        return {
            "applicable": False,
            "note": "Not applicable for existing evidence-backed molecules.",
        }
    return {
        "applicable": True,
        "notice": GENERATED_DIRECT_EVIDENCE_NOTICE,
        "generation_summary": _safe_payload(item.generation_summary or {}),
    }


def _uncertainties(item: Any) -> list[str]:
    uncertainties: list[str] = []
    if item.candidate_origin == "generated":
        uncertainties.append(GENERATED_DIRECT_EVIDENCE_NOTICE)
    if not item.literature_summary or not _citation_metadata(item.literature_summary):
        uncertainties.append("Literature support may be incomplete or absent.")
    if item.confidence is None or item.confidence < 0.7:
        uncertainties.append("Model confidence is limited and requires expert interpretation.")
    if item.risk_flags:
        uncertainties.append("Risk flags require expert review before any handoff.")
    if not item.canonical_smiles:
        uncertainties.append("Structure metadata is missing from the review item.")
    return uncertainties or ["No additional uncertainty notes were generated."]


def _default_followup_questions(item: Any) -> list[str]:
    questions = [
        "What evidence most directly supports the disease-target rationale?",
        "What evidence conflicts with or limits the candidate rationale?",
        "What warning or developability flags need specialist review?",
    ]
    if item.candidate_origin == "generated":
        questions.insert(
            0,
            "What additional computational checks are needed for this generated hypothesis?",
        )
    return questions


def _limitations_for_item(item: Any) -> list[str]:
    limitations = list(REVIEW_LIMITATIONS)
    if item.candidate_origin == "generated":
        limitations.insert(0, GENERATED_DIRECT_EVIDENCE_NOTICE)
    else:
        limitations.insert(0, EXISTING_ORIGIN_NOTICE)
    return limitations


def _citation_metadata(literature_summary: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = literature_summary.get("items") or literature_summary.get("citations") or []
    if not isinstance(raw_items, list):
        return []
    citations: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        citation = {
            key: raw[key]
            for key in sorted(_CITATION_KEYS)
            if key in raw and raw[key] not in (None, "", [])
        }
        if citation:
            citations.append(citation)
    return citations


def _artifact_paths(workspace: ReviewWorkspace, item: Any) -> dict[str, str]:
    paths: dict[str, str] = {}
    for source in (workspace.metadata, item.metadata):
        raw_paths = source.get("artifact_paths") if isinstance(source, dict) else None
        if isinstance(raw_paths, dict):
            paths.update(
                {
                    str(key): str(value)
                    for key, value in raw_paths.items()
                    if not _contains_procedural_term(str(key))
                    and not _contains_procedural_term(str(value))
                }
            )
    return paths


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _safe_payload(item)
            for key, item in value.items()
            if str(key).lower() not in _OMITTED_TEXT_KEYS
            and not _contains_procedural_term(str(key))
            and not should_omit_experimental_output_key(str(key))
        }
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    if isinstance(value, str):
        sanitized = sanitize_experimental_output_text(value)
        if _contains_procedural_term(sanitized):
            return "[omitted procedural or treatment detail]"
        return sanitized
    return value


def _contains_procedural_term(value: str) -> bool:
    lowered = re.sub(r"[_\\/\-]+", " ", value.lower())
    return any(term in lowered for term in _PROCEDURAL_TERMS)


def _render_content_markdown(content: Any) -> list[str]:
    if isinstance(content, dict):
        lines: list[str] = []
        for key, value in content.items():
            label = str(key).replace("_", " ").capitalize()
            if isinstance(value, list):
                lines.append(f"**{label}:**")
                if value:
                    lines.extend(f"- {_format_markdown_value(item)}" for item in value)
                else:
                    lines.append("- None recorded.")
            elif isinstance(value, dict):
                lines.append(f"**{label}:**")
                lines.append("```json")
                lines.append(json.dumps(value, indent=2, sort_keys=True))
                lines.append("```")
            else:
                lines.append(f"**{label}:** {_format_markdown_value(value)}")
        return lines
    if isinstance(content, list):
        return [f"- {_format_markdown_value(item)}" for item in content]
    return [_format_markdown_value(content)]


def _format_markdown_value(value: Any) -> str:
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    if value is None:
        return "None recorded."
    return str(value)


def _legacy_sections(dossier: CandidateDossier) -> list[dict[str, Any]]:
    return [
        {"title": "Executive summary", "content": {"summary": dossier.executive_summary}},
        *dossier.evidence_sections,
        *dossier.risk_sections,
        {
            "title": "Reviewer decisions and comments",
            "content": {
                "decisions": [
                    decision.model_dump(mode="json")
                    for decision in dossier.reviewer_decisions
                ],
                "comments": [
                    comment.model_dump(mode="json") for comment in dossier.reviewer_comments
                ],
            },
        },
        {
            "title": "Limitations and disclaimers",
            "content": {"limitations": dossier.limitations},
        },
    ]


def _get_item(workspace: ReviewWorkspace, review_item_id: str):
    for item in workspace.review_items:
        if item.review_item_id == review_item_id:
            return item
    raise ValueError(f"Unknown review item: {review_item_id}")
