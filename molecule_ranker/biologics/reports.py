from __future__ import annotations

from typing import Any

from molecule_ranker.biologics.schemas import (
    AntibodyDevelopabilityAssessment,
    AntibodyNoveltyAssessment,
    BiologicCandidate,
    GeneratedAntibodyHypothesis,
)
from molecule_ranker.biologics.scoring import score_biologic_candidate


def build_biologic_report_card(
    *,
    candidate: BiologicCandidate,
    developability: AntibodyDevelopabilityAssessment | None = None,
    novelty: AntibodyNoveltyAssessment | None = None,
    hypothesis: GeneratedAntibodyHypothesis | None = None,
) -> dict[str, Any]:
    return {
        "biologic_id": candidate.biologic_id,
        "name": candidate.name,
        "biologic_type": candidate.biologic_type,
        "origin": candidate.origin,
        "target_symbols": candidate.target_symbols,
        "antigen_names": candidate.antigen_names,
        "disease_name": candidate.disease_name,
        "score": score_biologic_candidate(candidate),
        "direct_experimental_evidence": candidate.direct_experimental_evidence,
        "developability": developability.model_dump(mode="json") if developability else None,
        "novelty": novelty.model_dump(mode="json") if novelty else None,
        "generated_hypothesis": hypothesis.model_dump(mode="json") if hypothesis else None,
        "limitations": [
            "Biologic report cards are research-planning artifacts, not evidence.",
            "Generated antibodies are computational hypotheses only.",
        ],
    }


__all__ = ["build_biologic_report_card"]
