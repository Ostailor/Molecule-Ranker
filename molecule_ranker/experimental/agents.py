from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from molecule_ranker.experimental.schemas import (
    EXPERIMENTAL_LIMITATIONS,
    ActiveLearningRecommendation,
    ActiveLearningReport,
    AssayResult,
    CandidateRecalibration,
    CandidateRecalibrationReport,
)
from molecule_ranker.review.schemas import ReviewItem, ValidationHandoff
from molecule_ranker.schemas import GeneratedMoleculeHypothesis, MoleculeCandidate


class ExperimentalEvidenceAgent:
    """Links imported assay results and recalibrates scores without fabricating outcomes."""

    def link_results(
        self,
        results: Iterable[AssayResult],
        *,
        candidates: Iterable[MoleculeCandidate] = (),
        generated_candidates: Iterable[GeneratedMoleculeHypothesis] = (),
        review_items: Iterable[ReviewItem] = (),
        validation_handoffs: Iterable[ValidationHandoff] = (),
    ) -> list[AssayResult]:
        candidate_index = _candidate_index(candidates)
        generated_index = {
            generated.name.strip().lower(): generated for generated in generated_candidates
        }
        review_index = {item.review_item_id: item for item in review_items}
        review_by_candidate = {
            _clean(item.candidate_id): item for item in review_items if item.candidate_id
        }
        handoff_index = {handoff.handoff_id: handoff for handoff in validation_handoffs}
        linked: list[AssayResult] = []
        for result in results:
            update: dict[str, object] = {}
            candidate = _match_candidate(result, candidate_index)
            if candidate is not None:
                update["linked_candidate_name"] = candidate.name
                update["linked_candidate_id"] = _candidate_id(candidate)
            generated = None
            if result.generated_molecule_name:
                generated = generated_index.get(result.generated_molecule_name.strip().lower())
            if generated is not None:
                update["linked_generated_molecule_name"] = generated.name
            review = review_index.get(result.review_item_id or "")
            if review is None and (result.candidate_id or update.get("linked_candidate_id")):
                review = review_by_candidate.get(
                    _clean(str(result.candidate_id or update.get("linked_candidate_id")))
                )
            if review is not None:
                update["linked_review_item_id"] = review.review_item_id
            handoff = handoff_index.get(result.validation_handoff_id or "")
            if handoff is not None:
                update["linked_validation_handoff_id"] = handoff.handoff_id
            metadata = {
                **result.metadata,
                "linkage": {
                    "linked_by_candidate_id_or_name": candidate is not None,
                    "linked_generated_molecule": generated is not None,
                    "linked_review_item": review is not None,
                    "linked_validation_handoff": handoff is not None,
                },
            }
            linked.append(result.model_copy(update={**update, "metadata": metadata}))
        return linked

    def recalibrate_candidates(
        self,
        candidates: Iterable[MoleculeCandidate],
        results: Iterable[AssayResult],
    ) -> CandidateRecalibrationReport:
        valid_results = [result for result in results if _usable_for_scoring(result)]
        excluded = [
            result.result_id
            for result in results
            if result.validation_status != "valid" or result.outcome is None
        ]
        recalibrations: list[CandidateRecalibration] = []
        for candidate in candidates:
            matched = [
                result for result in valid_results if _result_matches_candidate(result, candidate)
            ]
            counts = Counter(result.outcome for result in matched if result.outcome)
            delta = _experimental_delta(counts)
            original = candidate.score
            recalibrated = None if original is None else round(_clamp(original + delta), 3)
            candidate_id = _candidate_id(candidate)
            recalibrations.append(
                CandidateRecalibration(
                    candidate_id=candidate_id,
                    candidate_name=candidate.name,
                    original_score=original,
                    recalibrated_score=recalibrated,
                    experimental_score_delta=round(delta, 3),
                    outcome_counts=dict(sorted(counts.items())),
                    evidence_result_ids=[result.result_id for result in matched],
                    explanation=_recalibration_explanation(matched, delta),
                )
            )
        limitations = list(EXPERIMENTAL_LIMITATIONS)
        if excluded:
            limitations.append("Incomplete or invalid assay rows were excluded")
        return CandidateRecalibrationReport(
            recalibrations=recalibrations,
            excluded_result_ids=excluded,
            limitations=limitations,
        )


class ActiveLearningAgent:
    """Suggests next candidates to test from score uncertainty and imported assay outcomes."""

    def recommend_next_candidates(
        self,
        candidates: Iterable[MoleculeCandidate],
        results: Iterable[AssayResult],
        *,
        top: int = 10,
    ) -> ActiveLearningReport:
        valid_results = [result for result in results if _usable_for_scoring(result)]
        recommendations: list[ActiveLearningRecommendation] = []
        for candidate in candidates:
            matched = [
                result for result in valid_results if _result_matches_candidate(result, candidate)
            ]
            counts = Counter(result.outcome for result in matched if result.outcome)
            information_gain = _expected_information_gain(counts)
            base_score = candidate.score if candidate.score is not None else 0.5
            priority = _clamp(0.6 * base_score + information_gain)
            recommendations.append(
                ActiveLearningRecommendation(
                    candidate_id=_candidate_id(candidate),
                    candidate_name=candidate.name,
                    priority_score=round(priority, 3),
                    expected_information_gain=round(information_gain, 3),
                    outcome_counts=dict(sorted(counts.items())),
                    rationale=_active_learning_rationale(counts),
                    evidence_gap=_evidence_gap(counts),
                )
            )
        recommendations.sort(
            key=lambda item: (item.priority_score, item.expected_information_gain),
            reverse=True,
        )
        return ActiveLearningReport(recommendations=recommendations[:top])


def _candidate_index(candidates: Iterable[MoleculeCandidate]) -> dict[str, MoleculeCandidate]:
    index: dict[str, MoleculeCandidate] = {}
    for candidate in candidates:
        index[_clean(candidate.name)] = candidate
        for value in candidate.identifiers.values():
            index[_clean(value)] = candidate
    return index


def _match_candidate(
    result: AssayResult,
    index: dict[str, MoleculeCandidate],
) -> MoleculeCandidate | None:
    for key in [result.candidate_id, result.linked_candidate_id, result.molecule_name]:
        if key and (candidate := index.get(_clean(key))):
            return candidate
    return None


def _candidate_id(candidate: MoleculeCandidate) -> str:
    for key in ["chembl", "pubchem", "drugbank"]:
        if key in candidate.identifiers:
            return candidate.identifiers[key]
    if candidate.identifiers:
        return next(iter(candidate.identifiers.values()))
    return candidate.name


def _result_matches_candidate(result: AssayResult, candidate: MoleculeCandidate) -> bool:
    candidate_id = _candidate_id(candidate)
    result_ids = {
        _clean(value)
        for value in [result.candidate_id, result.linked_candidate_id, result.molecule_name]
        if value
    }
    candidate_ids = {_clean(candidate_id), _clean(candidate.name)}
    candidate_ids.update(_clean(value) for value in candidate.identifiers.values())
    return bool(result_ids & candidate_ids)


def _usable_for_scoring(result: AssayResult) -> bool:
    return result.validation_status == "valid" and result.outcome is not None


def _experimental_delta(counts: Counter[str]) -> float:
    if not counts:
        return 0.0
    delta = 0.12 * counts.get("positive", 0)
    delta -= 0.15 * counts.get("negative", 0)
    delta -= 0.03 * counts.get("inconclusive", 0)
    return max(-0.25, min(0.25, delta))


def _expected_information_gain(counts: Counter[str]) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.25
    if counts.get("inconclusive", 0) or counts.get("failed", 0):
        return 0.18
    if counts.get("positive", 0) and counts.get("negative", 0):
        return 0.16
    return 0.05


def _recalibration_explanation(results: list[AssayResult], delta: float) -> str:
    if not results:
        return "No valid imported assay results were linked; score is unchanged."
    direction = "increased" if delta > 0 else "decreased" if delta < 0 else "left unchanged"
    return (
        f"Score {direction} using {len(results)} valid imported experimental result(s). "
        "This is prioritization evidence only and does not establish clinical efficacy."
    )


def _active_learning_rationale(counts: Counter[str]) -> str:
    if not counts:
        return (
            "Prioritized because no valid imported assay outcome is linked yet. "
            "No validation claim is made."
        )
    if counts.get("inconclusive", 0) or counts.get("failed", 0):
        return (
            "Prioritized because existing imported outcomes are inconclusive or failed, "
            "leaving high uncertainty."
        )
    return "Lower information gain because imported assay outcomes already exist."


def _evidence_gap(counts: Counter[str]) -> str:
    if not counts:
        return "No linked valid experimental evidence."
    if counts.get("inconclusive", 0) or counts.get("failed", 0):
        return "Linked outcomes do not resolve experimental uncertainty."
    return "At least one linked valid imported assay outcome exists."


def _clean(value: object) -> str:
    return str(value or "").strip().lower()


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
