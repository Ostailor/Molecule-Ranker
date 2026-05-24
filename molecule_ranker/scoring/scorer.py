from __future__ import annotations

from collections.abc import Iterable

from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate, ScoreBreakdown, Target


class TransparentEvidenceScorer:
    """Deterministic V0.0 scorer using only retrieved evidence already on candidates."""

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
        matched_targets = self._matched_targets(candidate, targets)
        warnings = [
            *candidate.warnings,
            "Scores are heuristic and require experimental validation.",
        ]

        disease_target_relevance = self._disease_target_relevance(matched_targets)
        molecule_target_evidence = self._molecule_target_evidence(candidate)
        mechanism_plausibility = self._mechanism_plausibility(candidate, matched_targets)
        clinical_precedence = self._clinical_precedence(candidate.development_status)
        safety_prior = self._safety_prior(candidate.development_status)
        data_quality = self._data_quality(candidate.evidence)
        novelty_or_repurposing_value = self._novelty_or_repurposing_value(candidate)
        confidence = self._confidence(candidate, matched_targets, molecule_target_evidence)

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

        final_score = round(
            (
                0.25 * disease_target_relevance
                + 0.20 * molecule_target_evidence
                + 0.20 * mechanism_plausibility
                + 0.10 * clinical_precedence
                + 0.10 * safety_prior
                + 0.10 * data_quality
                + 0.05 * novelty_or_repurposing_value
            ),
            3,
        )
        target_text = ", ".join(target.symbol for target in matched_targets) or "no matched target"
        score = ScoreBreakdown(
            disease_target_relevance=round(disease_target_relevance, 3),
            molecule_target_evidence=round(molecule_target_evidence, 3),
            mechanism_plausibility=round(mechanism_plausibility, 3),
            clinical_precedence=round(clinical_precedence, 3),
            safety_prior=round(safety_prior, 3),
            data_quality=round(data_quality, 3),
            novelty_or_repurposing_value=round(novelty_or_repurposing_value, 3),
            final_score=final_score,
            confidence=round(confidence, 3),
            explanation=(
                f"{candidate.name} was scored using retrieved evidence for targets "
                f"{target_text}. Components use disease-target scores from target discovery, "
                "molecule-target evidence from retrieved mechanism/activity/annotation records, "
                "retrieved development status, source diversity, and identifier completeness. "
                "This is a research prioritization heuristic, not a therapeutic claim."
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
        return self._clamp(
            sum(target.disease_relevance_score for target in matched_targets)
            / len(matched_targets)
        )

    def _molecule_target_evidence(self, candidate: MoleculeCandidate) -> float:
        relevant = [
            item
            for item in candidate.evidence
            if item.evidence_type.lower()
            in {
                "mechanism",
                "activity",
                "assay",
                "binding",
                "target_interaction",
                "indication",
            }
        ]
        if not relevant:
            return 0.0
        return self._clamp(sum(item.confidence for item in relevant) / len(relevant))

    def _mechanism_plausibility(
        self, candidate: MoleculeCandidate, matched_targets: list[Target]
    ) -> float:
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
                return 0.8
        return 0.35 if matched_targets else 0.1

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

    def _data_quality(self, evidence: list[EvidenceItem]) -> float:
        if not evidence:
            return 0.0
        average_confidence = sum(item.confidence for item in evidence) / len(evidence)
        evidence_count_score = min(len(evidence) / 2.0, 1.0)
        source_diversity_score = min(len({item.source for item in evidence}) / 2.0, 1.0)
        provenance_score = sum(1 for item in evidence if item.source_record_id) / len(evidence)
        return self._clamp(
            (2.0 / 3.0) * average_confidence
            + (1.0 / 9.0) * evidence_count_score
            + (1.0 / 9.0) * source_diversity_score
            + (1.0 / 9.0) * provenance_score
        )

    def _novelty_or_repurposing_value(self, candidate: MoleculeCandidate) -> float:
        status = (candidate.development_status or "").lower()
        indications = self._metadata_values(candidate.evidence, "indication")
        established_for_query = any(str(value).strip() for value in indications)
        if ("approved" in status or "max_phase_4" in status) and not established_for_query:
            return 0.7
        if "clinical" in status or "phase" in status or "max_phase" in status:
            return 0.6
        return 0.4

    def _confidence(
        self,
        candidate: MoleculeCandidate,
        matched_targets: list[Target],
        molecule_target_evidence: float,
    ) -> float:
        evidence_count_score = min(len(candidate.evidence) / 5.0, 1.0)
        source_diversity_score = min(len({item.source for item in candidate.evidence}) / 3.0, 1.0)
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

    def _clamp(self, value: float) -> float:
        return max(0.0, min(float(value), 1.0))
