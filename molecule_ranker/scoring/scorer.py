from __future__ import annotations

from collections.abc import Iterable

from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.evidence import (
    evidence_completeness,
    evidence_source_diversity,
    is_clinical_evidence,
    is_molecule_target_evidence,
    is_safety_warning,
    normalize_evidence,
    normalize_evidence_type,
)
from molecule_ranker.evidence.types import CHEMICAL_ANNOTATION
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate, ScoreBreakdown, Target


class TransparentEvidenceScorer:
    """Deterministic V0.1 scorer using only retrieved evidence already on candidates."""

    def score(
        self,
        candidates: list[MoleculeCandidate],
        targets: list[Target],
        *,
        top: int,
    ) -> list[MoleculeCandidate]:
        evidence_backed = [candidate for candidate in candidates if candidate.evidence]
        if not evidence_backed:
            raise NoCandidatesFoundError("No evidence-backed molecule candidates can be scored.")
        scored = [self._score_candidate(candidate, targets) for candidate in evidence_backed]
        scored.sort(key=lambda candidate: candidate.score or 0.0, reverse=True)
        return scored[:top]

    def _score_candidate(
        self, candidate: MoleculeCandidate, targets: list[Target]
    ) -> MoleculeCandidate:
        candidate = candidate.model_copy(
            update={"evidence": normalize_evidence(candidate.evidence)}
        )
        matched_targets = self._matched_targets(candidate, targets)
        literature_items = self._literature_items(candidate, matched_targets)
        warnings = [
            *candidate.warnings,
            "Scores are heuristic and require experimental validation.",
        ]

        disease_target_relevance = self._disease_target_relevance(matched_targets)
        molecule_target_evidence = self._molecule_target_evidence(candidate)
        mechanism_plausibility = self._mechanism_plausibility(candidate, matched_targets)
        clinical_precedence = self._clinical_precedence_from_evidence(candidate)
        safety_prior = self._safety_prior_from_evidence(candidate)
        data_quality = self._data_quality(candidate)
        novelty_or_repurposing_value = self._novelty_or_repurposing_value(candidate)
        literature_quality = self._literature_quality(candidate)
        confidence = self._confidence(candidate, matched_targets, molecule_target_evidence)
        completeness = evidence_completeness(candidate, targets)

        disease_target_relevance = self._apply_literature_disease_target_modifier(
            disease_target_relevance,
            literature_items,
        )
        molecule_target_evidence = self._apply_literature_molecule_target_modifier(
            molecule_target_evidence,
            candidate,
            matched_targets,
            literature_items,
        )
        mechanism_plausibility = self._apply_literature_mechanism_modifier(
            mechanism_plausibility,
            candidate,
            matched_targets,
            literature_items,
        )
        clinical_precedence = self._apply_literature_clinical_modifier(
            clinical_precedence,
            candidate,
            literature_items,
        )
        safety_prior = self._apply_literature_safety_modifier(
            safety_prior,
            literature_items,
        )
        data_quality = self._apply_literature_data_quality_modifier(
            data_quality,
            candidate,
            literature_items,
        )
        novelty_or_repurposing_value = self._apply_literature_novelty_modifier(
            novelty_or_repurposing_value,
            candidate,
            matched_targets,
            literature_items,
        )
        confidence = self._apply_literature_confidence_modifier(
            confidence,
            candidate,
            matched_targets,
            literature_items,
        )
        literature_quality = max(literature_quality, self._literature_quality_from_items(candidate))

        if not matched_targets:
            warnings.append("Missing disease-target overlap in retrieved evidence.")
        if molecule_target_evidence == 0:
            warnings.append("Missing molecule-target evidence.")
        if mechanism_plausibility <= 0.2:
            warnings.append("Missing mechanism evidence.")
        if not candidate.development_status:
            warnings.append("Missing development status.")
        if len(candidate.identifiers) < 1:
            warnings.append("Sparse identifiers.")
        if self._warning_evidence(candidate):
            warnings.append("Retrieved drug warning evidence lowers the safety prior.")
        if self._literature_safety_or_contradictory_items(literature_items):
            warnings.append("Literature safety or contradictory evidence lowers confidence.")

        base_score = (
            0.25 * disease_target_relevance
            + 0.20 * molecule_target_evidence
            + 0.20 * mechanism_plausibility
            + 0.10 * clinical_precedence
            + 0.10 * safety_prior
            + 0.10 * data_quality
            + 0.05 * novelty_or_repurposing_value
        )
        if literature_quality > 0:
            base_score = base_score + 0.1 * max(0.0, literature_quality - base_score)
        final_score = round(base_score, 3)
        target_text = ", ".join(target.symbol for target in matched_targets) or "no matched target"
        score = ScoreBreakdown(
            disease_target_relevance=round(disease_target_relevance, 3),
            molecule_target_evidence=round(molecule_target_evidence, 3),
            mechanism_plausibility=round(mechanism_plausibility, 3),
            clinical_precedence=round(clinical_precedence, 3),
            safety_prior=round(safety_prior, 3),
            data_quality=round(data_quality, 3),
            novelty_or_repurposing_value=round(novelty_or_repurposing_value, 3),
            literature_quality=round(literature_quality, 3),
            final_score=final_score,
            confidence=round(confidence, 3),
            explanation=self._explanation(
                candidate=candidate,
                target_text=target_text,
                completeness=completeness,
                literature_items=literature_items,
            ),
        )
        return candidate.model_copy(
            update={
                "score": score.final_score,
                "score_breakdown": score,
                "warnings": warnings,
            }
        )

    def _matched_targets(
        self, candidate: MoleculeCandidate, targets: list[Target]
    ) -> list[Target]:
        known = {target.upper() for target in candidate.known_targets}
        return [target for target in targets if target.symbol.upper() in known]

    def _disease_target_relevance(self, matched_targets: list[Target]) -> float:
        if not matched_targets:
            return 0.0
        scores = sorted(
            (self._target_relevance_score(target) for target in matched_targets),
            reverse=True,
        )
        weights = [0.5**index for index in range(len(scores))]
        weighted = sum(score * weight for score, weight in zip(scores, weights, strict=True))
        return self._clamp(weighted / sum(weights))

    def _molecule_target_evidence(self, candidate: MoleculeCandidate) -> float:
        mechanism_scores = [
            self._mechanism_evidence_score(item)
            for item in candidate.evidence
            if normalize_evidence_type(item.evidence_type) == "molecule_target_mechanism"
        ]
        activity_scores = [
            self._activity_evidence_score(item)
            for item in candidate.evidence
            if normalize_evidence_type(item.evidence_type) == "molecule_target_activity"
        ]
        if not mechanism_scores and not activity_scores:
            return 0.0
        mechanism_score = max(mechanism_scores, default=0.0)
        activity_score = max(activity_scores, default=0.0)
        if mechanism_score and activity_score:
            return self._clamp(0.45 * mechanism_score + 0.55 * activity_score + 0.05)
        return self._clamp(max(mechanism_score, 0.9 * activity_score))

    def _mechanism_plausibility(
        self, candidate: MoleculeCandidate, matched_targets: list[Target]
    ) -> float:
        mechanism_items = [
            item
            for item in candidate.evidence
            if normalize_evidence_type(item.evidence_type) == "molecule_target_mechanism"
        ]
        if mechanism_items:
            return self._clamp(
                max(self._mechanism_evidence_score(item) for item in mechanism_items)
            )
        activity_items = [
            item
            for item in candidate.evidence
            if normalize_evidence_type(item.evidence_type) == "molecule_target_activity"
        ]
        if activity_items:
            return self._clamp(
                0.35
                + 0.35 * max(self._activity_evidence_score(item) for item in activity_items)
            )
        if not candidate.mechanism_of_action:
            return 0.1
        mechanism_text = candidate.mechanism_of_action.lower()
        for target in matched_targets:
            target_terms = [target.symbol.lower()]
            if target.name:
                target_terms.append(target.name.lower())
            if target.mechanism:
                target_terms.append(target.mechanism.lower())
            if any(term and term in mechanism_text for term in target_terms):
                return 0.55
        return 0.25 if matched_targets else 0.1

    def _clinical_precedence(self, development_status: str | None) -> float:
        status = (development_status or "").lower()
        if "approved" in status or "max_phase_4" in status or "phase 4" in status:
            return 1.0
        if "phase 3" in status or "max_phase_3" in status:
            return 0.8
        if "phase 2" in status or "max_phase_2" in status:
            return 0.6
        if "phase 1" in status or "max_phase_1" in status:
            return 0.4
        if "preclinical" in status:
            return 0.25
        return 0.1

    def _safety_prior(self, development_status: str | None) -> float:
        status = (development_status or "").lower()
        if "approved" in status or "max_phase_4" in status or "phase 4" in status:
            return 0.8
        if "clinical" in status or "phase" in status or "max_phase" in status:
            return 0.5
        if "preclinical" in status:
            return 0.25
        return 0.1

    def _safety_prior_from_evidence(self, candidate: MoleculeCandidate) -> float:
        baseline = self._safety_prior(candidate.development_status)
        warning_items = self._warning_evidence(candidate)
        if not warning_items:
            return baseline
        severe = any(
            self._is_serious_warning(item)
            for item in warning_items
        )
        warning_penalty = 0.35 if severe else 0.2
        return self._clamp(max(0.1, baseline - warning_penalty))

    def _clinical_precedence_from_evidence(self, candidate: MoleculeCandidate) -> float:
        phases = [
            value
            for value in (
                self._metadata_float(item, "max_phase_for_ind")
                for item in candidate.evidence
                if is_clinical_evidence(item)
            )
            if value is not None
        ]
        phases.extend(
            value
            for value in (
                self._metadata_float(item, "molecule_max_phase") for item in candidate.evidence
            )
            if value is not None
        )
        chemical_phase = self._metadata_float_from_mapping(candidate.chemical_metadata, "max_phase")
        if chemical_phase is not None:
            phases.append(chemical_phase)
        status_score = self._clinical_precedence(candidate.development_status)
        if not phases:
            return status_score
        evidence_phase_score = self._clinical_precedence(f"max_phase_{int(max(phases))}")
        return max(status_score, evidence_phase_score)

    def _data_quality(self, candidate: MoleculeCandidate) -> float:
        evidence = self._data_quality_evidence(candidate.evidence)
        if not evidence:
            return 0.0
        average_confidence = sum(item.confidence for item in evidence) / len(evidence)
        evidence_count_score = min(len(evidence) / 5.0, 1.0)
        source_diversity_score = evidence_source_diversity(evidence)
        provenance_score = sum(1 for item in evidence if item.source_record_id) / len(evidence)
        identifier_score = min(len(candidate.identifiers) / 4.0, 1.0)
        mapping_confidence = self._mapping_confidence(evidence)
        timestamp_score = sum(1 for item in evidence if item.retrieval_timestamp) / len(evidence)
        return self._clamp(
            0.25 * average_confidence
            + 0.15 * evidence_count_score
            + 0.15 * source_diversity_score
            + 0.15 * provenance_score
            + 0.15 * identifier_score
            + 0.10 * mapping_confidence
            + 0.05 * timestamp_score
        )

    def _novelty_or_repurposing_value(self, candidate: MoleculeCandidate) -> float:
        status = (candidate.development_status or "").lower()
        clinical_items = [item for item in candidate.evidence if is_clinical_evidence(item)]
        target_rationale = any(is_molecule_target_evidence(item) for item in candidate.evidence)
        if any(self._indication_overlaps_query(item) for item in clinical_items):
            return 0.25
        if ("approved" in status or "max_phase_4" in status) and target_rationale:
            return 0.75
        if "clinical" in status or "phase" in status or "max_phase" in status:
            return 0.6
        if self._molecule_target_evidence(candidate) > 0.8:
            return 0.5
        return 0.3

    def _confidence(
        self,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
        molecule_target_evidence: float,
    ) -> float:
        evidence = self._data_quality_evidence(candidate.evidence)
        evidence_count_score = min(len(evidence) / 5.0, 1.0)
        source_diversity_score = evidence_source_diversity(evidence)
        identifier_score = min(len(candidate.identifiers) / 3.0, 1.0)
        has_disease_target = 1.0 if matched_targets else 0.0
        has_molecule_target = 1.0 if molecule_target_evidence > 0 else 0.0
        return self._clamp(
            0.25 * evidence_count_score
            + 0.20 * source_diversity_score
            + 0.20 * identifier_score
            + 0.20 * has_disease_target
            + 0.15 * has_molecule_target
        )

    def _metadata_values(self, evidence: Iterable[EvidenceItem], key: str) -> list[object]:
        return [item.metadata[key] for item in evidence if key in item.metadata]

    def _metadata_float(self, evidence: EvidenceItem, key: str) -> float | None:
        value = evidence.metadata.get(key)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _warning_evidence(self, candidate: MoleculeCandidate) -> list[EvidenceItem]:
        return [
            item
            for item in candidate.evidence
            if is_safety_warning(item) or item.evidence_type == "literature_safety"
        ]

    def _literature_items(
        self,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
    ) -> list[EvidenceItem]:
        items = [
            item for item in candidate.evidence if self._is_literature_evidence(item)
        ]
        for target in matched_targets:
            items.extend(
                item for item in target.evidence if self._is_literature_evidence(item)
            )
        return items

    def _is_literature_evidence(self, item: EvidenceItem) -> bool:
        return item.evidence_type.startswith("literature_")

    def _data_quality_evidence(self, evidence: list[EvidenceItem]) -> list[EvidenceItem]:
        return [
            item
            for item in evidence
            if not (
                self._is_literature_evidence(item)
                and (
                    self._literature_is_retracted(item)
                    or self._literature_evidence_level(item) == "mention_only"
                )
            )
        ]

    def _literature_is_retracted(self, item: EvidenceItem) -> bool:
        return item.metadata.get("is_retracted") is True

    def _literature_evidence_level(self, item: EvidenceItem) -> str:
        return str(item.metadata.get("evidence_level") or "").lower()

    def _literature_study_type(self, item: EvidenceItem) -> str:
        return str(item.metadata.get("study_type") or "").lower()

    def _literature_direction(self, item: EvidenceItem) -> str:
        return str(item.metadata.get("direction") or "").lower()

    def _literature_claim_type(self, item: EvidenceItem) -> str:
        return str(item.metadata.get("claim_type") or "").lower()

    def _is_positive_literature(self, item: EvidenceItem) -> bool:
        if self._literature_is_retracted(item):
            return False
        if item.evidence_type in {"literature_safety", "literature_contradictory"}:
            return False
        if self._literature_direction(item) in {"contradictory", "safety_concern"}:
            return False
        if self._literature_evidence_level(item) in {
            "contradictory",
            "safety_concern",
            "mention_only",
        }:
            return False
        if self._literature_claim_type(item) == "mention_only":
            return False
        return True

    def _is_mention_only_literature(self, item: EvidenceItem) -> bool:
        return (
            item.evidence_type == "literature_mention"
            or self._literature_evidence_level(item) == "mention_only"
            or self._literature_claim_type(item) == "mention_only"
        )

    def _literature_strength(self, item: EvidenceItem) -> float:
        if self._literature_is_retracted(item):
            return 0.0
        if self._is_mention_only_literature(item):
            return self._clamp(0.2 * item.confidence)
        evidence_level = self._literature_evidence_level(item)
        level_bonus = {"high": 0.15, "medium": 0.05, "low": -0.1}.get(evidence_level, 0.0)
        strength = self._clamp(item.confidence + level_bonus)
        if self._literature_study_type(item) == "review":
            strength = min(strength, 0.45)
        return strength

    def _apply_literature_disease_target_modifier(
        self,
        baseline: float,
        literature_items: list[EvidenceItem],
    ) -> float:
        supporting = [
            item
            for item in literature_items
            if item.evidence_type == "literature_disease_target"
            and self._is_positive_literature(item)
        ]
        mentions = [
            item
            for item in literature_items
            if item.evidence_type == "literature_disease_target"
            and self._is_mention_only_literature(item)
        ]
        if supporting:
            strength = max(self._literature_strength(item) for item in supporting)
            return self._clamp(baseline + min(0.08, 0.06 * strength))
        if mentions:
            return self._clamp(baseline + 0.01)
        return baseline

    def _apply_literature_molecule_target_modifier(
        self,
        baseline: float,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
        literature_items: list[EvidenceItem],
    ) -> float:
        supporting = [
            item
            for item in literature_items
            if item.evidence_type in {"literature_molecule_target", "literature_mechanism"}
            and self._is_positive_literature(item)
            and self._literature_matches_candidate_target(item, candidate, matched_targets)
        ]
        mentions = [
            item
            for item in literature_items
            if item.evidence_type in {"literature_molecule_target", "literature_mechanism"}
            and self._is_mention_only_literature(item)
            and self._literature_matches_candidate_target(item, candidate, matched_targets)
        ]
        if supporting:
            strength = max(self._literature_strength(item) for item in supporting)
            return self._clamp(baseline + min(0.12, 0.10 * strength))
        if mentions:
            return self._clamp(baseline + 0.01)
        return baseline

    def _apply_literature_mechanism_modifier(
        self,
        baseline: float,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
        literature_items: list[EvidenceItem],
    ) -> float:
        supporting = [
            item
            for item in literature_items
            if item.evidence_type == "literature_mechanism"
            and self._is_positive_literature(item)
            and self._literature_matches_candidate_target(item, candidate, matched_targets)
        ]
        mentions = [
            item
            for item in literature_items
            if item.evidence_type == "literature_mechanism"
            and self._is_mention_only_literature(item)
            and self._literature_matches_candidate_target(item, candidate, matched_targets)
        ]
        if supporting:
            strength = max(self._literature_strength(item) for item in supporting)
            return self._clamp(baseline + min(0.12, 0.12 * strength))
        if mentions:
            return self._clamp(baseline + 0.01)
        return baseline

    def _apply_literature_clinical_modifier(
        self,
        baseline: float,
        candidate: MoleculeCandidate,
        literature_items: list[EvidenceItem],
    ) -> float:
        clinical_items = [
            item
            for item in literature_items
            if item.evidence_type == "literature_clinical"
            and self._is_positive_literature(item)
            and self._literature_matches_candidate_disease(item, candidate)
            and self._literature_study_type(item) != "review"
        ]
        if not clinical_items:
            return baseline
        best = max(self._clinical_literature_score(item) for item in clinical_items)
        return self._clamp(max(baseline, best))

    def _clinical_literature_score(self, item: EvidenceItem) -> float:
        study_type = self._literature_study_type(item)
        evidence_level = self._literature_evidence_level(item)
        if study_type == "clinical_trial" and evidence_level == "high":
            return 0.65
        if study_type == "clinical_trial":
            return 0.55
        if study_type == "observational_human":
            return 0.4
        if study_type == "case_report":
            return 0.2
        return 0.1

    def _apply_literature_safety_modifier(
        self,
        baseline: float,
        literature_items: list[EvidenceItem],
    ) -> float:
        safety_items = [
            item
            for item in literature_items
            if not self._literature_is_retracted(item)
            and (
                item.evidence_type == "literature_safety"
                or self._literature_direction(item) == "safety_concern"
            )
        ]
        contradictory_items = [
            item
            for item in literature_items
            if not self._literature_is_retracted(item)
            and (
                item.evidence_type == "literature_contradictory"
                or self._literature_direction(item) == "contradictory"
            )
        ]
        penalty = 0.0
        if safety_items:
            penalty = max(penalty, 0.18)
        if contradictory_items:
            penalty = max(penalty, 0.10)
        return self._clamp(max(0.05, baseline - penalty))

    def _apply_literature_data_quality_modifier(
        self,
        baseline: float,
        candidate: MoleculeCandidate,
        literature_items: list[EvidenceItem],
    ) -> float:
        quality_items = [
            item
            for item in literature_items
            if self._is_positive_literature(item)
            and not self._is_mention_only_literature(item)
        ]
        if not quality_items:
            return baseline
        citation_score = sum(self._citation_completeness(item) for item in quality_items) / len(
            quality_items
        )
        non_retracted_score = (
            sum(1 for item in quality_items if not self._literature_is_retracted(item))
            / len(quality_items)
        )
        consistency_bonus = (
            0.02
            if self._database_and_literature_agree(candidate, [], quality_items)
            else 0.0
        )
        return self._clamp(
            baseline
            + min(0.08, 0.04 * citation_score + 0.02 * non_retracted_score)
            + consistency_bonus
        )

    def _apply_literature_novelty_modifier(
        self,
        baseline: float,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
        literature_items: list[EvidenceItem],
    ) -> float:
        if any(
            item.evidence_type == "literature_clinical"
            and self._is_positive_literature(item)
            and self._literature_matches_candidate_disease(item, candidate)
            for item in literature_items
        ):
            return self._clamp(max(baseline, 0.55))
        if any(
            item.evidence_type == "literature_molecule_disease"
            and self._is_positive_literature(item)
            and self._literature_matches_candidate_disease(item, candidate)
            for item in literature_items
        ):
            return self._clamp(max(baseline, 0.5))
        if any(
            item.evidence_type in {"literature_molecule_target", "literature_mechanism"}
            and self._is_positive_literature(item)
            and self._literature_matches_candidate_target(item, candidate, matched_targets)
            for item in literature_items
        ):
            return self._clamp(max(baseline, 0.45))
        if any(self._is_mention_only_literature(item) for item in literature_items):
            return self._clamp(min(max(baseline, 0.25), 0.35))
        return baseline

    def _apply_literature_confidence_modifier(
        self,
        baseline: float,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
        literature_items: list[EvidenceItem],
    ) -> float:
        confidence = baseline
        supportive = [
            item
            for item in literature_items
            if self._is_positive_literature(item)
            and (
                self._literature_matches_candidate_target(item, candidate, matched_targets)
                or self._literature_matches_candidate_disease(item, candidate)
            )
        ]
        if supportive and self._database_and_literature_agree(
            candidate,
            matched_targets,
            supportive,
        ):
            confidence += 0.05
        if any(self._is_mention_only_literature(item) for item in literature_items):
            confidence -= 0.03
        if self._literature_safety_or_contradictory_items(literature_items):
            confidence -= 0.15
        if any(self._literature_is_retracted(item) for item in literature_items):
            confidence -= 0.08
        return self._clamp(confidence)

    def _literature_safety_or_contradictory_items(
        self,
        literature_items: list[EvidenceItem],
    ) -> list[EvidenceItem]:
        return [
            item
            for item in literature_items
            if not self._literature_is_retracted(item)
            and (
                item.evidence_type in {"literature_safety", "literature_contradictory"}
                or self._literature_direction(item)
                in {"safety_concern", "contradictory"}
            )
        ]

    def _literature_matches_candidate_target(
        self,
        item: EvidenceItem,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
    ) -> bool:
        if not self._literature_mentions_candidate(item, candidate.name):
            return False
        target_symbols = [target.symbol for target in matched_targets]
        return any(self._literature_mentions_term(item, target) for target in target_symbols)

    def _literature_matches_candidate_disease(
        self,
        item: EvidenceItem,
        candidate: MoleculeCandidate,
    ) -> bool:
        return self._literature_mentions_candidate(
            item, candidate.name
        ) and bool(str(item.metadata.get("disease_name") or "").strip())

    def _literature_mentions_candidate(self, item: EvidenceItem, candidate_name: str) -> bool:
        metadata_name = str(item.metadata.get("candidate_name") or "")
        return metadata_name.lower() == candidate_name.lower() or self._literature_mentions_term(
            item,
            candidate_name,
        )

    def _literature_mentions_term(self, item: EvidenceItem, term: str | None) -> bool:
        if not term:
            return False
        metadata_values = [
            item.metadata.get("target_symbol"),
            item.metadata.get("target_name"),
            item.metadata.get("candidate_name"),
            item.metadata.get("disease_name"),
        ]
        if any(str(value).lower() == term.lower() for value in metadata_values if value):
            return True
        text = " ".join([item.summary, str(item.metadata.get("query_text") or "")]).lower()
        return term.lower() in text

    def _citation_completeness(self, item: EvidenceItem) -> float:
        identifiers = [
            item.metadata.get("pmid"),
            item.metadata.get("doi"),
            item.metadata.get("pmcid"),
        ]
        citation = item.metadata.get("citation")
        if isinstance(citation, dict):
            identifiers.extend([citation.get("pmid"), citation.get("doi"), citation.get("pmcid")])
        return min(sum(1 for value in identifiers if value) / 3.0, 1.0)

    def _database_and_literature_agree(
        self,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
        literature_items: list[EvidenceItem],
    ) -> bool:
        has_database_target = any(is_molecule_target_evidence(item) for item in candidate.evidence)
        if has_database_target and any(
            item.evidence_type in {"literature_molecule_target", "literature_mechanism"}
            and self._literature_matches_candidate_target(item, candidate, matched_targets)
            for item in literature_items
        ):
            return True
        has_database_clinical = any(is_clinical_evidence(item) for item in candidate.evidence)
        return has_database_clinical and any(
            item.evidence_type == "literature_clinical"
            and self._literature_matches_candidate_disease(item, candidate)
            for item in literature_items
        )

    def _target_relevance_score(self, target: Target) -> float:
        evidence_scores = [
            self._metadata_float(item, "association_score")
            for item in target.evidence
        ]
        real_scores = [score for score in evidence_scores if score is not None]
        return self._clamp(max([target.disease_relevance_score, *real_scores]))

    def _mechanism_evidence_score(self, evidence: EvidenceItem) -> float:
        mapping_confidence = self._single_mapping_confidence(evidence)
        direct_bonus = 0.05 if evidence.metadata.get("direct_interaction") else 0.0
        action_bonus = 0.03 if evidence.metadata.get("action_type") else 0.0
        blended = self._clamp(
            0.75 * evidence.confidence
            + 0.20 * mapping_confidence
            + direct_bonus
            + action_bonus
        )
        return max(evidence.confidence, blended)

    def _activity_evidence_score(self, evidence: EvidenceItem) -> float:
        pchembl = self._metadata_float(evidence, "pchembl_value")
        pchembl_score = self._clamp((pchembl or 5.0) / 9.0)
        assay_confidence = self._assay_confidence(evidence)
        mapping_confidence = self._single_mapping_confidence(evidence)
        standard_bonus = 1.0 if evidence.metadata.get("standard_type") else 0.6
        blended = self._clamp(
            0.30 * evidence.confidence
            + 0.30 * pchembl_score
            + 0.20 * assay_confidence
            + 0.15 * mapping_confidence
            + 0.05 * standard_bonus
        )
        return max(evidence.confidence, blended)

    def _mapping_confidence(self, evidence: list[EvidenceItem]) -> float:
        values = [self._single_mapping_confidence(item) for item in evidence]
        return max(values, default=0.5)

    def _single_mapping_confidence(self, evidence: EvidenceItem) -> float:
        value = (
            self._metadata_float(evidence, "target_mapping_confidence")
            or self._metadata_float(evidence, "mapping_confidence")
            or self._metadata_float(evidence, "target_mapper_confidence")
        )
        return self._clamp(value if value is not None else 0.5)

    def _assay_confidence(self, evidence: EvidenceItem) -> float:
        value = (
            self._metadata_float(evidence, "assay_confidence_score")
            or self._metadata_float(evidence, "confidence_score")
        )
        if value is None:
            return 0.5
        return self._clamp(value / 9.0 if value > 1 else value)

    def _indication_overlaps_query(self, evidence: EvidenceItem) -> bool:
        for key in (
            "query_disease_match",
            "matches_query_disease",
            "indication_matches_query",
        ):
            if evidence.metadata.get(key) is True:
                return True
        return False

    def _metadata_float_from_mapping(self, metadata: dict[str, object], key: str) -> float | None:
        value = metadata.get(key)
        if value in (None, ""):
            return None
        if not isinstance(value, (str, int, float)):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _chemical_annotation_evidence(self, candidate: MoleculeCandidate) -> list[EvidenceItem]:
        return [
            item
            for item in candidate.evidence
            if normalize_evidence_type(item.evidence_type) == CHEMICAL_ANNOTATION
        ]

    def _is_serious_warning(self, item: EvidenceItem) -> bool:
        text = " ".join(
            [
                str(item.metadata.get("warning_type") or ""),
                str(item.metadata.get("warning_class") or ""),
                item.summary,
            ]
        ).lower()
        return any(
            term in text
            for term in ("black box", "boxed", "contraindication", "withdrawn", "fatal")
        )

    def _explanation(
        self,
        *,
        candidate: MoleculeCandidate,
        target_text: str,
        completeness: dict[str, bool],
        literature_items: list[EvidenceItem],
    ) -> str:
        dimensions = [
            "Open Targets disease-target association scores",
            "normalized ChEMBL mechanism evidence",
            "ChEMBL activity evidence",
            "assay confidence",
            "target mapping confidence",
            "pChEMBL/activity quality",
            "clinical phase and indication metadata",
            "source diversity",
            "identifier completeness",
        ]
        if self._warning_evidence(candidate):
            dimensions.append("ChEMBL safety warnings")
        if any(self._indication_overlaps_query(item) for item in candidate.evidence):
            dimensions.append("indication overlap with the queried disease")
        if self._chemical_annotation_evidence(candidate):
            dimensions.append("PubChem chemical annotation")
        literature_summary = self._literature_explanation(literature_items)
        if not literature_summary:
            literature_summary = self._legacy_literature_explanation(candidate)
        if literature_summary:
            dimensions.append("literature evidence from retrieved paper records")
        missing = []
        if not completeness["has_molecule_target_evidence"]:
            missing.append("missing molecule-target evidence")
        if not completeness["has_matched_target"]:
            missing.append("missing disease-target overlap")
        if not completeness["has_identifier"]:
            missing.append("missing stable identifiers")
        missing_text = f" Limitations: {', '.join(missing)}." if missing else ""
        return (
            f"{candidate.name} was scored using retrieved evidence for targets {target_text}. "
            f"Evidence dimensions used: {', '.join(dimensions)}.{literature_summary}"
            f"{missing_text} "
            "This is a research prioritization heuristic, not a therapeutic claim."
        )

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))

    def _literature_quality(self, candidate: MoleculeCandidate) -> float:
        bundle = candidate.literature_evidence
        if bundle is None or not bundle.items:
            return 0.0
        return self._clamp(bundle.quality_score)

    def _literature_quality_from_items(self, candidate: MoleculeCandidate) -> float:
        literature_items = [
            item
            for item in candidate.evidence
            if self._is_literature_evidence(item)
            and not self._literature_is_retracted(item)
        ]
        if not literature_items:
            return 0.0
        scores = []
        for item in literature_items:
            if self._is_mention_only_literature(item):
                scores.append(0.05)
            elif item.evidence_type in {"literature_safety", "literature_contradictory"}:
                scores.append(0.0)
            else:
                scores.append(0.5 * self._literature_strength(item))
        return self._clamp(sum(scores) / len(scores))

    def _literature_explanation(self, literature_items: list[EvidenceItem]) -> str:
        if not literature_items:
            return ""
        paper_ids = {
            str(item.metadata.get("paper_id") or item.source_record_id)
            for item in literature_items
            if item.metadata.get("paper_id") or item.source_record_id
        }
        study_counts = {
            "clinical": 0,
            "preclinical": 0,
            "review": 0,
            "safety": 0,
            "contradictory": 0,
            "mention-only": 0,
        }
        for item in literature_items:
            study_type = self._literature_study_type(item)
            if "clinical" in study_type or study_type in {"observational_human", "case_report"}:
                study_counts["clinical"] += 1
            if study_type in {"animal_preclinical", "in_vitro", "computational"}:
                study_counts["preclinical"] += 1
            if study_type == "review":
                study_counts["review"] += 1
            if item.evidence_type == "literature_safety":
                study_counts["safety"] += 1
            if item.evidence_type == "literature_contradictory":
                study_counts["contradictory"] += 1
            if self._is_mention_only_literature(item):
                study_counts["mention-only"] += 1
        detail = ", ".join(
            f"{label}: {count}"
            for label, count in study_counts.items()
            if count
        )
        detail_text = f" ({detail})" if detail else ""
        return (
            f" Literature evidence: {len(paper_ids)} literature papers and "
            f"{len(literature_items)} claims contributed{detail_text}."
        )

    def _legacy_literature_explanation(self, candidate: MoleculeCandidate) -> str:
        bundle = candidate.literature_evidence
        if bundle is None or not bundle.items:
            return ""
        claim_count = sum(len(item.claims) for item in bundle.items)
        study_counts: dict[str, int] = {}
        for item in bundle.items:
            for claim in item.claims:
                study_type = claim.study_type or "unknown"
                study_counts[study_type] = study_counts.get(study_type, 0) + 1
        detail = ", ".join(
            f"{label}: {count}"
            for label, count in sorted(study_counts.items())
            if count
        )
        detail_text = f" ({detail})" if detail else ""
        return (
            f" Literature evidence: {len(bundle.items)} literature papers and "
            f"{claim_count} claims contributed{detail_text}."
        )
