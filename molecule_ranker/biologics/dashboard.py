from __future__ import annotations

from typing import Any

from molecule_ranker.biologics.schemas import BiologicCandidate

BIOLOGICS_DASHBOARD_PAGES: tuple[dict[str, str], ...] = (
    {
        "page_id": "overview",
        "label": "Biologics overview",
        "route": "/biologics/overview",
    },
    {
        "page_id": "biologic_candidates",
        "label": "Biologic candidates",
        "route": "/biologics/candidates",
    },
    {
        "page_id": "antibody_sequences",
        "label": "Antibody sequences",
        "route": "/biologics/sequences",
    },
    {
        "page_id": "cdr_annotations",
        "label": "CDR annotations",
        "route": "/biologics/cdr-annotations",
    },
    {
        "page_id": "developability_flags",
        "label": "Developability flags",
        "route": "/biologics/developability",
    },
    {
        "page_id": "novelty",
        "label": "Novelty",
        "route": "/biologics/novelty",
    },
    {
        "page_id": "generated_antibody_hypotheses",
        "label": "Generated antibody hypotheses",
        "route": "/biologics/generated-antibodies",
    },
    {
        "page_id": "biologics_review_queue",
        "label": "Biologics review queue",
        "route": "/biologics/review-queue",
    },
    {
        "page_id": "biologics_result_bundle",
        "label": "Biologics result bundle",
        "route": "/biologics/result-bundle",
    },
)


def build_biologics_dashboard_summary(
    candidates: list[BiologicCandidate],
) -> dict[str, Any]:
    generated = [candidate for candidate in candidates if candidate.origin == "generated"]
    return {
        "candidate_count": len(candidates),
        "generated_candidate_count": len(generated),
        "direct_experimental_evidence_count": sum(
            1 for candidate in candidates if candidate.direct_experimental_evidence
        ),
        "generated_direct_experimental_evidence_count": sum(
            1 for candidate in generated if candidate.direct_experimental_evidence
        ),
        "warnings": [
            "Dashboard summaries are operational views, not scientific evidence.",
            "Generated antibodies are computational hypotheses only.",
        ],
    }


def build_biologics_dashboard_pages() -> list[dict[str, str]]:
    return [dict(page) for page in BIOLOGICS_DASHBOARD_PAGES]


def build_biologics_dashboard_snapshot(
    candidates: list[BiologicCandidate] | None = None,
) -> dict[str, Any]:
    candidate_list = candidates or []
    return {
        "summary": build_biologics_dashboard_summary(candidate_list),
        "pages": build_biologics_dashboard_pages(),
        "generated_antibody_label": "Generated antibody hypotheses",
        "warnings": [
            "Generated antibodies are computational hypotheses only.",
            "Dashboard pages do not imply binding, safety, developability, or manufacturability.",
        ],
    }


__all__ = [
    "BIOLOGICS_DASHBOARD_PAGES",
    "build_biologics_dashboard_pages",
    "build_biologics_dashboard_snapshot",
    "build_biologics_dashboard_summary",
]
