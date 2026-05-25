from __future__ import annotations

from typing import Any

import pytest

from molecule_ranker.data_sources.errors import NoCandidatesFoundError
from molecule_ranker.schemas import (
    DevelopabilityAssessment,
    EvidenceItem,
    MoleculeCandidate,
    Target,
)
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


def _developability(
    *,
    score: float,
    risk_level: str = "low",
    recommendation: str = "favorable_hypothesis",
) -> DevelopabilityAssessment:
    return DevelopabilityAssessment(
        molecule_name="candidate",
        origin="existing",
        structure_available=True,
        canonical_smiles="CCO",
        developability_score=score,
        triage_recommendation=recommendation,  # type: ignore[arg-type]
        metadata={"risk_level": risk_level},
    )


def _mechanism_evidence(
    record_id: str,
    target: str,
    confidence: float = 0.8,
    *,
    action_type: str = "INHIBITOR",
    mapping_confidence: float | None = None,
) -> EvidenceItem:
    metadata: dict[str, Any] = {"target": target, "action_type": action_type}
    if mapping_confidence is not None:
        metadata["target_mapping_confidence"] = mapping_confidence
    return EvidenceItem(
        source="ChEMBL",
        source_record_id=record_id,
        title="Mechanism record",
        evidence_type="mechanism",
        summary=f"Mocked mechanism evidence for {target}.",
        confidence=confidence,
        metadata=metadata,
    )


def _activity_evidence(
    record_id: str,
    *,
    pchembl_value: float | None = 8.0,
    assay_confidence: float | None = 8.0,
    mapping_confidence: float | None = 0.9,
    confidence: float = 0.8,
) -> EvidenceItem:
    metadata = {
        "standard_type": "IC50",
        "standard_value": 12.0,
        "standard_units": "nM",
    }
    if pchembl_value is not None:
        metadata["pchembl_value"] = pchembl_value
    if assay_confidence is not None:
        metadata["assay_confidence_score"] = assay_confidence
    if mapping_confidence is not None:
        metadata["target_mapping_confidence"] = mapping_confidence
    return EvidenceItem(
        source="ChEMBL",
        source_record_id=record_id,
        title="Activity record",
        evidence_type="activity",
        summary="Mocked ChEMBL activity evidence.",
        confidence=confidence,
        metadata=metadata,
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
    assert breakdown.data_quality == pytest.approx(0.71)
    assert breakdown.novelty_or_repurposing_value == pytest.approx(0.6)
    expected = (
        0.25 * 0.8
        + 0.20 * 0.8
        + 0.20 * 0.8
        + 0.10 * 0.6
        + 0.10 * 0.5
        + 0.10 * 0.71
        + 0.05 * 0.6
    )
    assert breakdown.final_score == pytest.approx(round(expected, 3))
    assert scored.score == breakdown.final_score
    assert "heuristic" in " ".join(scored.warnings).lower()
    assert "LRRK2" in breakdown.explanation


def test_existing_developability_is_bounded_modifier_not_evidence_replacement():
    targets = [_target("LRRK2", 0.8, mechanism="kinase signaling")]
    candidate = _candidate(
        name="Developability qualified",
        known_targets=["LRRK2"],
        development_status="phase 2",
        mechanism_of_action="LRRK2 kinase inhibitor",
        evidence=[
            _mechanism_evidence("mec-1", "LRRK2", 0.8),
            _annotation_evidence("123", 0.6),
        ],
        identifiers={"chembl": "CHEMBL1", "pubchem_cid": "123"},
    )
    baseline = TransparentEvidenceScorer().score([candidate], targets, top=1)[0]
    with_developability = candidate.model_copy(
        update={"developability_assessment": _developability(score=0.2)}
    )

    scored = TransparentEvidenceScorer().score([with_developability], targets, top=1)[0]

    assert baseline.score is not None
    assert scored.score_breakdown is not None
    assert scored.score == pytest.approx(round(baseline.score * (0.85 + 0.15 * 0.2), 3))
    assert (
        "Disease/target evidence remains separate from developability risk."
        in scored.score_breakdown.explanation
    )


def test_critical_developability_caps_existing_score_and_high_risk_reduces_confidence():
    targets = [_target("LRRK2", 0.95, mechanism="kinase signaling")]
    candidate = _candidate(
        name="Critical risk",
        known_targets=["LRRK2"],
        development_status="approved",
        mechanism_of_action="LRRK2 kinase inhibitor",
        evidence=[
            _mechanism_evidence("mec-1", "LRRK2", 0.95),
            _activity_evidence("act-1", confidence=0.95),
        ],
        identifiers={"chembl": "CHEMBL1", "pubchem_cid": "123", "inchikey": "KEY"},
    )
    high_risk = candidate.model_copy(
        update={
            "developability_assessment": _developability(
                score=0.8,
                risk_level="high",
                recommendation="high_risk_flags",
            )
        }
    )
    critical = candidate.model_copy(
        update={
            "developability_assessment": _developability(
                score=0.8,
                risk_level="critical",
                recommendation="high_risk_flags",
            )
        }
    )

    baseline = TransparentEvidenceScorer().score([candidate], targets, top=1)[0]
    high_scored = TransparentEvidenceScorer().score([high_risk], targets, top=1)[0]
    critical_scored = TransparentEvidenceScorer().score([critical], targets, top=1)[0]

    assert baseline.score_breakdown is not None
    assert high_scored.score_breakdown is not None
    assert high_scored.score is not None
    assert critical_scored.score is not None
    assert critical_scored.score <= 0.35
    assert high_scored.score_breakdown.confidence <= baseline.score_breakdown.confidence - 0.15
    assert 0.0 <= high_scored.score <= 1.0
    assert 0.0 <= critical_scored.score <= 1.0


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


def test_scoring_uses_activity_indication_and_warning_evidence_dimensions():
    targets = [_target("LRRK2", 0.9)]
    candidate = _candidate(
        name="Richer evidence",
        known_targets=["LRRK2"],
        development_status=None,
        mechanism_of_action="LRRK2 inhibitor",
        evidence=[
            _mechanism_evidence("mec-1", "LRRK2", 0.8),
            EvidenceItem(
                source="ChEMBL",
                source_record_id="act-1",
                title="Potent activity",
                evidence_type="activity",
                summary="Activity record with pChEMBL value.",
                confidence=0.9,
                metadata={"pchembl_value": 8.0, "standard_type": "IC50"},
            ),
            EvidenceItem(
                source="ChEMBL",
                source_record_id="ind-1",
                title="Clinical indication",
                evidence_type="indication",
                summary="Indication record.",
                confidence=0.8,
                metadata={"max_phase_for_ind": 3, "indication": "Disease"},
            ),
            EvidenceItem(
                source="ChEMBL",
                source_record_id="warn-1",
                title="Drug warning",
                evidence_type="safety_warning",
                summary="Black box warning.",
                confidence=0.9,
                metadata={"warning_type": "Black Box Warning", "warning_class": "boxed_warning"},
            ),
        ],
        identifiers={"chembl": "CHEMBL1", "pubchem_cid": "123", "inchikey": "KEY"},
    )

    scored = TransparentEvidenceScorer().score([candidate], targets, top=1)[0]

    assert scored.score_breakdown is not None
    breakdown = scored.score_breakdown
    assert breakdown.molecule_target_evidence > 0.85
    assert breakdown.clinical_precedence == pytest.approx(0.8)
    assert breakdown.safety_prior < 0.5
    assert any("warning" in warning.lower() for warning in scored.warnings)


def test_activity_quality_increases_molecule_target_evidence():
    targets = [_target("LRRK2", 0.8)]
    mechanism_only = _candidate(
        name="Mechanism only",
        known_targets=["LRRK2"],
        development_status="phase 1",
        mechanism_of_action="LRRK2 inhibitor",
        evidence=[_mechanism_evidence("mec-1", "LRRK2", confidence=0.55)],
        identifiers={"chembl": "CHEMBL_MECH"},
    )
    with_activity = _candidate(
        name="Mechanism plus activity",
        known_targets=["LRRK2"],
        development_status="phase 1",
        mechanism_of_action="LRRK2 inhibitor",
        evidence=[
            _mechanism_evidence("mec-1", "LRRK2", confidence=0.55),
            _activity_evidence(
                "act-1",
                pchembl_value=8.4,
                assay_confidence=9,
                mapping_confidence=0.95,
                confidence=0.75,
            ),
        ],
        identifiers={"chembl": "CHEMBL_ACT"},
    )

    scored = TransparentEvidenceScorer().score([mechanism_only, with_activity], targets, top=2)
    by_name = {candidate.name: candidate for candidate in scored}

    mechanism_score = by_name["Mechanism only"].score_breakdown
    activity_score = by_name["Mechanism plus activity"].score_breakdown
    assert mechanism_score is not None
    assert activity_score is not None
    assert activity_score.molecule_target_evidence > mechanism_score.molecule_target_evidence
    assert activity_score.mechanism_plausibility >= mechanism_score.mechanism_plausibility
    assert "pchembl" in activity_score.explanation.lower()
    assert "mapping confidence" in activity_score.explanation.lower()


def test_indication_overlap_lowers_novelty_relative_to_repurposing_candidate():
    targets = [_target("LRRK2", 0.8)]
    known_for_query = _candidate(
        name="Known indication",
        known_targets=["LRRK2"],
        development_status="approved",
        mechanism_of_action="LRRK2 inhibitor",
        evidence=[
            _mechanism_evidence("mec-known", "LRRK2"),
            EvidenceItem(
                source="ChEMBL",
                source_record_id="ind-known",
                title="Known indication",
                evidence_type="indication",
                summary="Retrieved indication overlaps the queried disease.",
                confidence=0.8,
                metadata={"indication": "Parkinson disease", "query_disease_match": True},
            ),
        ],
        identifiers={"chembl": "CHEMBL_KNOWN"},
    )
    repurposing = _candidate(
        name="Repurposing candidate",
        known_targets=["LRRK2"],
        development_status="approved",
        mechanism_of_action="LRRK2 inhibitor",
        evidence=[
            _mechanism_evidence("mec-repurpose", "LRRK2"),
            EvidenceItem(
                source="ChEMBL",
                source_record_id="ind-other",
                title="Other indication",
                evidence_type="indication",
                summary="Retrieved indication is for another disease.",
                confidence=0.8,
                metadata={"indication": "Other disease", "query_disease_match": False},
            ),
        ],
        identifiers={"chembl": "CHEMBL_REPURPOSE"},
    )

    scored = TransparentEvidenceScorer().score([known_for_query, repurposing], targets, top=2)
    by_name = {candidate.name: candidate for candidate in scored}

    known_breakdown = by_name["Known indication"].score_breakdown
    repurpose_breakdown = by_name["Repurposing candidate"].score_breakdown
    assert known_breakdown is not None
    assert repurpose_breakdown is not None
    assert known_breakdown.novelty_or_repurposing_value < (
        repurpose_breakdown.novelty_or_repurposing_value
    )
    assert "indication overlap" in known_breakdown.explanation.lower()


def test_missing_required_evidence_keeps_confidence_low_and_explanation_specific():
    candidate = _candidate(
        name="Sparse annotation",
        known_targets=[],
        development_status=None,
        mechanism_of_action=None,
        evidence=[_annotation_evidence("cid-only", 0.4)],
        identifiers={},
    )

    scored = TransparentEvidenceScorer().score([candidate], [_target("LRRK2", 0.8)], top=1)[0]

    assert scored.score_breakdown is not None
    assert scored.score_breakdown.confidence < 0.35
    assert scored.score_breakdown.molecule_target_evidence == 0.0
    explanation = scored.score_breakdown.explanation.lower()
    assert "missing molecule-target evidence" in explanation
    assert "chemical annotation" in explanation


def test_scoring_lack_of_warning_data_does_not_increase_safety_prior():
    targets = [_target("LRRK2", 0.9)]
    candidate = _candidate(
        name="No warning data",
        known_targets=["LRRK2"],
        development_status="approved",
        mechanism_of_action="LRRK2 inhibitor",
        evidence=[_mechanism_evidence("mec-1", "LRRK2", 0.8)],
    )

    scored = TransparentEvidenceScorer().score([candidate], targets, top=1)[0]

    assert scored.score_breakdown is not None
    assert scored.score_breakdown.safety_prior == pytest.approx(0.8)
    assert not any("warning evidence lowers" in warning.lower() for warning in scored.warnings)


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
