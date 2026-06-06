from __future__ import annotations

from molecule_ranker.biologics.schemas import BiologicCandidate


def is_antibody_like(candidate: BiologicCandidate) -> bool:
    return candidate.biologic_type in {
        "monoclonal_antibody",
        "bispecific_antibody",
        "nanobody",
        "antibody_fragment",
    }


def requires_generated_antibody_review(candidate: BiologicCandidate) -> bool:
    return candidate.origin == "generated" or not candidate.direct_experimental_evidence


__all__ = ["is_antibody_like", "requires_generated_antibody_review"]
