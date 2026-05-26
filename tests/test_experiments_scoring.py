from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.agents.base import PipelineContext
from molecule_ranker.agents.evidence_scoring import EvidenceScoringAgent
from molecule_ranker.experiments.scoring import experimental_score_modifiers
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate, Target
from molecule_ranker.scoring.scorer import TransparentEvidenceScorer


def _target() -> Target:
    return Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.8,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id="MONDO:MAOB",
                title="Target association",
                evidence_type="target_disease_association",
                summary="Disease target association.",
                confidence=0.8,
            )
        ],
    )


def _mechanism() -> EvidenceItem:
    return EvidenceItem(
        source="ChEMBL",
        source_record_id="mec-1",
        title="Mechanism",
        evidence_type="mechanism",
        summary="MAOB mechanism evidence.",
        confidence=0.8,
        metadata={"target": "MAOB", "action_type": "INHIBITOR"},
    )


def _candidate(evidence: list[EvidenceItem] | None = None) -> MoleculeCandidate:
    return MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        development_status="phase 2",
        mechanism_of_action="MAOB inhibitor",
        evidence=[_mechanism(), *(evidence or [])],
    )


def _experimental(
    evidence_type: str,
    *,
    result_id: str,
    confidence: float = 0.8,
    qc_status: str = "passed",
    activity_direction: str = "active",
    outcome_label: str = "positive",
) -> EvidenceItem:
    return EvidenceItem(
        source="Imported experimental result",
        source_record_id=result_id,
        title="Binding screen result for Rasagiline",
        evidence_type=evidence_type,
        summary="Imported assay result reports outcome for Rasagiline.",
        confidence=confidence,
        metadata={
            "result_id": result_id,
            "outcome_label": outcome_label,
            "activity_direction": activity_direction,
            "qc_status": qc_status,
            "endpoint_name": "binding_affinity",
        },
    )


def _score(candidate: MoleculeCandidate) -> MoleculeCandidate:
    return TransparentEvidenceScorer().score([candidate], [_target()], top=1)[0]


def test_experimental_positive_qc_passed_increases_score_modestly():
    baseline = _score(_candidate())
    scored = _score(_candidate([_experimental("experimental_positive", result_id="pos-1")]))

    assert baseline.score is not None
    assert scored.score is not None
    assert scored.score > baseline.score
    assert scored.score - baseline.score <= 0.08
    assert scored.score_breakdown is not None
    assert scored.score_breakdown.experimental_evidence_score > 0
    assert scored.score_breakdown.experimental_evidence_confidence > 0


def test_experimental_negative_result_lowers_score():
    baseline = _score(_candidate())
    scored = _score(
        _candidate(
            [
                _experimental(
                    "experimental_negative",
                    result_id="neg-1",
                    activity_direction="inactive",
                    outcome_label="negative",
                )
            ]
        )
    )

    assert baseline.score is not None
    assert scored.score is not None
    assert scored.score < baseline.score
    assert scored.score_breakdown is not None
    assert scored.score_breakdown.experimental_evidence_score < 0


def test_experimental_safety_result_lowers_safety_prior():
    baseline = _score(_candidate())
    scored = _score(
        _candidate(
            [
                _experimental(
                    "experimental_safety_concern",
                    result_id="tox-1",
                    activity_direction="toxic",
                    outcome_label="negative",
                )
            ]
        )
    )

    assert baseline.score_breakdown is not None
    assert scored.score_breakdown is not None
    assert scored.score_breakdown.safety_prior < baseline.score_breakdown.safety_prior
    assert scored.score_breakdown.experimental_evidence_score < 0


def test_failed_qc_does_not_increase_score():
    baseline = _score(_candidate())
    scored = _score(
        _candidate(
            [
                _experimental(
                    "experimental_failed_qc",
                    result_id="fail-1",
                    confidence=0.0,
                    qc_status="failed",
                    activity_direction="ambiguous",
                    outcome_label="failed_qc",
                )
            ]
        )
    )

    assert baseline.score is not None
    assert scored.score is not None
    assert scored.score <= baseline.score
    assert scored.score_breakdown is not None
    assert scored.score_breakdown.experimental_evidence_score == 0


def test_experimental_score_modifiers_ignore_unrelated_context_support():
    relevant = experimental_score_modifiers(
        [_experimental("experimental_positive", result_id="pos-1")],
        context_relevant=True,
    )
    unrelated = experimental_score_modifiers(
        [_experimental("experimental_positive", result_id="pos-1")],
        context_relevant=False,
    )

    assert unrelated.support_score < relevant.support_score
    assert unrelated.score_delta < relevant.score_delta


def _generated_candidate(**metadata: Any) -> MoleculeCandidate:
    return MoleculeCandidate(
        name="Generated-MAOB-001",
        molecule_type="small_molecule",
        origin="generated",
        identifiers={"generated": "Generated-MAOB-001"},
        known_targets=["MAOB"],
        score=0.5,
        generation_metadata={"generation_score": 0.5, **metadata},
        warnings=["Generated hypothesis; no direct activity evidence."],
    )


def test_generated_exact_positive_result_sets_direct_evidence_flag_and_increases_score():
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_generated_candidate()],
        config={
            "experimental_evidence": {
                "generated_summaries": {
                    "Generated-MAOB-001": {
                        "positive_count": 1,
                        "negative_count": 0,
                        "failed_qc_count": 0,
                        "metadata": {"direct_evidence_result_ids": ["generated-result"]},
                    }
                }
            }
        },
    )

    updated = EvidenceScoringAgent().run(context)
    generated = updated.candidates[0]

    assert generated.score is not None
    assert generated.score > 0.5
    assert generated.generation_metadata["experimental_direct_evidence_available"] is True
    assert generated.generation_metadata["direct_experimental_result_ids"] == [
        "generated-result"
    ]


def test_seed_result_does_not_increase_generated_direct_evidence():
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_generated_candidate()],
        config={
            "experimental_evidence": {
                "candidate_summaries": {
                    "Rasagiline": {
                        "positive_count": 1,
                        "metadata": {"direct_evidence_result_ids": ["seed-result"]},
                    }
                },
                "generated_summaries": {},
            }
        },
    )

    updated = EvidenceScoringAgent().run(context)
    generated = updated.candidates[0]

    assert generated.score == pytest.approx(0.5)
    assert generated.generation_metadata.get("experimental_direct_evidence_available") is not True
