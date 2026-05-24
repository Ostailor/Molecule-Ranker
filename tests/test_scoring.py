from __future__ import annotations

import pytest

from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.schemas import EvidenceItem, MoleculeCandidate, Target
from molecule_ranker.scoring.scorer import TransparentEvidenceScorer


def _target(symbol: str, score: float, mechanism: str | None = None) -> Target:
    return Target(
        symbol=symbol,
        name=f"{symbol} target",
        disease_relevance_score=score,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id=f"MONDO_TEST:{symbol}",
                title="Disease target association",
                evidence_type="target_disease_association",
                summary="Mocked disease-target association.",
                confidence=score,
                metadata={"query": "test"},
            )
        ],
        mechanism=mechanism,
    )


def _candidate(
    *,
    name: str,
    known_targets: list[str],
    development_status: str | None,
    mechanism_of_action: str | None,
    evidence: list[EvidenceItem],
    identifiers: dict[str, str] | None = None,
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": f"CHEMBL_{name}"} if identifiers is None else identifiers,
        known_targets=known_targets,
        development_status=development_status,
        mechanism_of_action=mechanism_of_action,
        evidence=evidence,
        score=None,
        score_breakdown=None,
        warnings=[],
    )


def _mechanism_evidence(record_id: str, target: str, confidence: float = 0.8) -> EvidenceItem:
    return EvidenceItem(
        source="ChEMBL",
        source_record_id=record_id,
        title="Mechanism record",
        evidence_type="mechanism",
        summary=f"Mocked mechanism evidence for {target}.",
        confidence=confidence,
        metadata={"target": target, "action_type": "INHIBITOR"},
    )


def _annotation_evidence(record_id: str = "2244", confidence: float = 0.6) -> EvidenceItem:
    return EvidenceItem(
        source="PubChem",
        source_record_id=record_id,
        title="Chemical annotation",
        evidence_type="chemical_annotation",
        summary="Mocked PubChem annotation.",
        confidence=confidence,
        metadata={"cid": record_id},
    )


def test_scoring_formula_uses_real_retrieved_evidence_components():
    targets = [_target("LRRK2", 0.8, mechanism="kinase signaling")]
    candidate = _candidate(
        name="Clinical candidate",
        known_targets=["LRRK2"],
        development_status="phase 2",
        mechanism_of_action="LRRK2 kinase inhibitor",
        evidence=[
            _mechanism_evidence("mec-1", "LRRK2", 0.8),
            _annotation_evidence("123", 0.6),
        ],
        identifiers={"chembl": "CHEMBL1", "pubchem_cid": "123"},
    )

    scored = TransparentEvidenceScorer().score([candidate], targets, top=1)[0]

    assert scored.score_breakdown is not None
    breakdown = scored.score_breakdown
    assert breakdown.disease_target_relevance == pytest.approx(0.8)
    assert breakdown.molecule_target_evidence == pytest.approx(0.8)
    assert breakdown.mechanism_plausibility == pytest.approx(0.8)
    assert breakdown.clinical_precedence == pytest.approx(0.6)
    assert breakdown.safety_prior == pytest.approx(0.5)
    assert breakdown.data_quality == pytest.approx(0.8)
    assert breakdown.novelty_or_repurposing_value == pytest.approx(0.6)
    expected = (
        0.25 * 0.8
        + 0.20 * 0.8
        + 0.20 * 0.8
        + 0.10 * 0.6
        + 0.10 * 0.5
        + 0.10 * 0.8
        + 0.05 * 0.6
    )
    assert breakdown.final_score == pytest.approx(round(expected, 3))
    assert scored.score == breakdown.final_score
    assert "heuristic" in " ".join(scored.warnings).lower()
    assert "LRRK2" in breakdown.explanation


def test_scoring_sorts_by_final_score_and_all_scores_stay_in_range():
    targets = [_target("LRRK2", 0.9), _target("SNCA", 0.5)]
    strong = _candidate(
        name="Strong",
        known_targets=["LRRK2"],
        development_status="approved",
        mechanism_of_action="LRRK2 modulator",
        evidence=[_mechanism_evidence("mec-strong", "LRRK2", 0.9)],
    )
    weak = _candidate(
        name="Weak",
        known_targets=["SNCA"],
        development_status=None,
        mechanism_of_action=None,
        evidence=[_annotation_evidence("weak", 0.3)],
    )

    scored = TransparentEvidenceScorer().score([weak, strong], targets, top=2)

    assert [candidate.name for candidate in scored] == ["Strong", "Weak"]
    for candidate in scored:
        assert candidate.score is not None
        assert 0 <= candidate.score <= 1
        assert candidate.score_breakdown is not None
        for value in candidate.score_breakdown.model_dump(exclude={"explanation"}).values():
            assert 0 <= value <= 1


def test_scoring_adds_warnings_for_missing_evidence_dimensions():
    targets = [_target("LRRK2", 0.9)]
    candidate = _candidate(
        name="Sparse",
        known_targets=[],
        development_status=None,
        mechanism_of_action=None,
        evidence=[_annotation_evidence("sparse", 0.3)],
        identifiers={},
    )

    scored = TransparentEvidenceScorer().score([candidate], targets, top=1)[0]

    warning_text = " ".join(scored.warnings).lower()
    assert "missing disease-target overlap" in warning_text
    assert "missing molecule-target evidence" in warning_text
    assert "missing mechanism evidence" in warning_text
    assert "missing development status" in warning_text
    assert "sparse identifiers" in warning_text


def test_scoring_rejects_candidates_with_no_real_evidence():
    candidate = _candidate(
        name="No evidence",
        known_targets=["LRRK2"],
        development_status="approved",
        mechanism_of_action="LRRK2 modulator",
        evidence=[],
    )

    with pytest.raises(NoCandidatesFoundError):
        TransparentEvidenceScorer().score([candidate], [_target("LRRK2", 0.9)], top=1)
