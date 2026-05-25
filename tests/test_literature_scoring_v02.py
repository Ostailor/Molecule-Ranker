from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate, Target
from molecule_ranker.scoring.scorer import TransparentEvidenceScorer


def _target(score: float = 0.75) -> Target:
    return Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=score,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id="MONDO:MAOB",
                title="Disease target association",
                evidence_type="target_disease_association",
                summary="Database disease-target association.",
                confidence=score,
            )
        ],
    )


def _candidate(
    *,
    evidence: list[EvidenceItem] | None = None,
    development_status: str | None = None,
    mechanism_of_action: str | None = None,
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887", "pubchem_cid": "3052776"},
        known_targets=["MAOB"],
        development_status=development_status,
        mechanism_of_action=mechanism_of_action,
        evidence=[
            EvidenceItem(
                source="PubChem",
                source_record_id="3052776",
                title="Chemical annotation",
                evidence_type="chemical_annotation",
                summary="Retrieved PubChem annotation.",
                confidence=0.6,
            ),
            *(evidence or []),
        ],
    )


def _mechanism_evidence() -> EvidenceItem:
    return EvidenceItem(
        source="ChEMBL",
        source_record_id="mec-1",
        title="Mechanism",
        evidence_type="mechanism",
        summary="Database mechanism evidence.",
        confidence=0.7,
        metadata={"target": "MAOB", "action_type": "INHIBITOR"},
    )


def _literature(
    evidence_type: str,
    *,
    confidence: float = 0.7,
    study_type: str = "animal_preclinical",
    evidence_level: str = "medium",
    direction: str = "supportive",
    claim_type: str = "mechanism_support",
    is_retracted: bool = False,
    candidate_name: str = "Rasagiline",
    target_symbol: str = "MAOB",
    disease_name: str = "Parkinson disease",
    relation_cues: dict[str, list[str]] | None = None,
) -> EvidenceItem:
    metadata: dict[str, Any] = {
        "citation": {"pmid": "12345", "doi": "10.1000/example", "pmcid": "PMC123"},
        "paper_id": "pubmed:12345",
        "pmid": "12345",
        "doi": "10.1000/example",
        "pmcid": "PMC123",
        "publication_type": "Journal Article",
        "study_type": study_type,
        "evidence_level": evidence_level,
        "supporting_snippet": "Rasagiline inhibits MAOB in Parkinson disease.",
        "query_id": "lit-0001",
        "query_text": "Rasagiline AND MAOB AND Parkinson disease",
        "claim_type": claim_type,
        "direction": direction,
        "is_retracted": is_retracted,
        "cited_by_count": 7,
        "candidate_name": candidate_name,
        "target_symbol": target_symbol,
        "disease_name": disease_name,
        "relation_cues": relation_cues
        or {
            "supportive_mechanism": ["inhibits"],
            "clinical": [],
            "safety_concern": [],
            "negative_or_contradictory": [],
        },
    }
    return EvidenceItem(
        source="PubMed",
        source_record_id="12345",
        title="Literature record",
        evidence_type=evidence_type,
        summary="PubMed record 12345 mentions Rasagiline and MAOB with relation cue 'inhibits'.",
        confidence=confidence,
        metadata=metadata,
    )


def _score(candidate: MoleculeCandidate) -> MoleculeCandidate:
    return TransparentEvidenceScorer().score([candidate], [_target()], top=1)[0]


def test_supportive_literature_raises_mechanism_plausibility_modestly() -> None:
    baseline = _score(_candidate())
    with_literature = _score(
        _candidate(
            evidence=[
                _literature(
                    "literature_mechanism",
                    claim_type="mechanism_support",
                    evidence_level="medium",
                )
            ]
        )
    )

    assert baseline.score_breakdown is not None
    assert with_literature.score_breakdown is not None
    increase = (
        with_literature.score_breakdown.mechanism_plausibility
        - baseline.score_breakdown.mechanism_plausibility
    )
    assert 0 < increase <= 0.15


def test_clinical_literature_raises_precedence_only_for_disease_molecule_evidence() -> None:
    direct = _score(
        _candidate(
            evidence=[
                _literature(
                    "literature_clinical",
                    study_type="clinical_trial",
                    evidence_level="high",
                    claim_type="clinical_support",
                )
            ]
        )
    )
    target_only = _score(
        _candidate(
            evidence=[
                _literature(
                    "literature_clinical",
                    study_type="clinical_trial",
                    evidence_level="high",
                    claim_type="clinical_support",
                    disease_name="",
                )
            ]
        )
    )

    assert direct.score_breakdown is not None
    assert target_only.score_breakdown is not None
    assert direct.score_breakdown.clinical_precedence > 0.1
    assert target_only.score_breakdown.clinical_precedence == pytest.approx(0.1)


def test_safety_literature_lowers_safety_prior() -> None:
    baseline = _score(_candidate(development_status="phase 2"))
    with_safety = _score(
        _candidate(
            development_status="phase 2",
            evidence=[
                _literature(
                    "literature_safety",
                    direction="safety_concern",
                    claim_type="safety_concern",
                    evidence_level="safety_concern",
                    relation_cues={
                        "supportive_mechanism": [],
                        "clinical": [],
                        "safety_concern": ["hepatotoxicity"],
                        "negative_or_contradictory": [],
                    },
                )
            ],
        )
    )

    assert baseline.score_breakdown is not None
    assert with_safety.score_breakdown is not None
    assert with_safety.score_breakdown.safety_prior < baseline.score_breakdown.safety_prior


def test_mention_only_literature_barely_changes_score() -> None:
    baseline = _score(_candidate())
    with_mention = _score(
        _candidate(
            evidence=[
                _literature(
                    "literature_mention",
                    confidence=0.35,
                    evidence_level="mention_only",
                    direction="neutral",
                    claim_type="mention_only",
                    relation_cues={
                        "supportive_mechanism": [],
                        "clinical": [],
                        "safety_concern": [],
                        "negative_or_contradictory": [],
                    },
                )
            ]
        )
    )

    assert baseline.score is not None
    assert with_mention.score is not None
    assert abs(with_mention.score - baseline.score) <= 0.02


def test_contradictory_literature_lowers_confidence() -> None:
    baseline = _score(_candidate(evidence=[_mechanism_evidence()]))
    contradictory = _score(
        _candidate(
            evidence=[
                _mechanism_evidence(),
                _literature(
                    "literature_contradictory",
                    confidence=0.7,
                    direction="contradictory",
                    claim_type="negative_or_contradictory",
                    evidence_level="contradictory",
                    relation_cues={
                        "supportive_mechanism": [],
                        "clinical": [],
                        "safety_concern": [],
                        "negative_or_contradictory": ["did not improve"],
                    },
                ),
            ]
        )
    )

    assert baseline.score_breakdown is not None
    assert contradictory.score_breakdown is not None
    assert contradictory.score_breakdown.confidence < baseline.score_breakdown.confidence


def test_retracted_literature_does_not_improve_score() -> None:
    baseline = _score(_candidate())
    retracted = _score(
        _candidate(
            evidence=[
                _literature(
                    "literature_mechanism",
                    confidence=0.0,
                    evidence_level="low",
                    is_retracted=True,
                )
            ]
        )
    )

    assert baseline.score is not None
    assert retracted.score is not None
    assert retracted.score <= baseline.score


def test_all_scores_remain_in_range_with_literature_modifiers() -> None:
    scored = _score(
        _candidate(
            development_status="phase 2",
            mechanism_of_action="MAOB inhibitor",
            evidence=[
                _mechanism_evidence(),
                _literature("literature_disease_target", claim_type="disease_target_association"),
                _literature("literature_mechanism", claim_type="mechanism_support"),
                _literature(
                    "literature_clinical",
                    study_type="clinical_trial",
                    evidence_level="high",
                    claim_type="clinical_support",
                ),
                _literature(
                    "literature_safety",
                    direction="safety_concern",
                    claim_type="safety_concern",
                    evidence_level="safety_concern",
                ),
            ],
        )
    )

    assert scored.score_breakdown is not None
    for value in scored.score_breakdown.model_dump(exclude={"explanation"}).values():
        assert 0 <= value <= 1
    assert "literature papers" in scored.score_breakdown.explanation.lower()
    assert "clinical" in scored.score_breakdown.explanation.lower()
    assert "safety" in scored.score_breakdown.explanation.lower()
