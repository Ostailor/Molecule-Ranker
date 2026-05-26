from __future__ import annotations

from typing import Any

from molecule_ranker.review.queue_builder import build_review_workspace
from molecule_ranker.schemas import (
    AgentTrace,
    DevelopabilityAssessment,
    DevelopabilityFlag,
    Disease,
    EvidenceClaim,
    EvidenceItem,
    GeneratedMoleculeHypothesis,
    LiteratureEvidenceBundle,
    LiteratureEvidenceItem,
    LiteraturePaper,
    LiteratureQuery,
    MoleculeCandidate,
    RankingRun,
    ScoreBreakdown,
    Target,
)


def _disease() -> Disease:
    return Disease(input_name="PD", canonical_name="Parkinson disease")


def _target() -> Target:
    return Target(
        symbol="MAOB",
        name="Monoamine oxidase B",
        disease_relevance_score=0.85,
        evidence=[
            EvidenceItem(
                source="OpenTargets",
                source_record_id="target-1",
                title="Target evidence",
                evidence_type="genetic_association",
                summary="Disease target association.",
                confidence=0.8,
            )
        ],
    )


def _score(**overrides: Any) -> ScoreBreakdown:
    payload: dict[str, Any] = {
        "disease_target_relevance": 0.85,
        "molecule_target_evidence": 0.82,
        "mechanism_plausibility": 0.7,
        "clinical_precedence": 0.55,
        "safety_prior": 0.7,
        "data_quality": 0.8,
        "novelty_or_repurposing_value": 0.35,
        "literature_quality": 0.72,
        "developability_score": 0.7,
        "final_score": 0.8,
        "confidence": 0.76,
        "explanation": "Strong evidence-backed candidate.",
    }
    payload.update(overrides)
    return ScoreBreakdown(**payload)


def _literature_bundle(
    *,
    contradictory: bool = False,
    absent: bool = False,
) -> LiteratureEvidenceBundle:
    if absent:
        return LiteratureEvidenceBundle(
            candidate_name="Candidate",
            absent_reason="No papers found.",
        )
    claim = EvidenceClaim(
        claim_type="contradictory" if contradictory else "mechanistic_support",
        text="Conflicting report." if contradictory else "Supports target rationale.",
        study_type="preclinical",
        support_level="contradicts" if contradictory else "supports",
    )
    item = LiteratureEvidenceItem(
        query=LiteratureQuery(
            disease="Parkinson disease",
            molecule="Candidate",
            target="MAOB",
            query_text="Candidate MAOB Parkinson",
        ),
        paper=LiteraturePaper(
            source="PubMed",
            source_record_id="PMID1",
            title="Literature record",
        ),
        citation=LiteraturePaper(
            source="PubMed",
            source_record_id="PMID1",
            title="Literature record",
        ).to_citation(),
        claims=[claim],
        quality_score=0.75,
    )
    return LiteratureEvidenceBundle(
        candidate_name="Candidate",
        query_count=1,
        items=[item],
        quality_score=0.75,
    )


def _developability(
    *,
    risk: str = "low",
    structure_available: bool = True,
    critical: bool = False,
) -> DevelopabilityAssessment:
    severity = "high" if critical else "low"
    return DevelopabilityAssessment(
        molecule_name="Candidate",
        origin="existing",
        structure_available=structure_available,
        canonical_smiles="CCO" if structure_available else None,
        developability_score=0.7 if risk == "low" else 0.2,
        triage_recommendation="high_risk_flags" if critical else "favorable_hypothesis",
        toxicity_risk_flags=[
            DevelopabilityFlag(
                category="toxicity_risk",
                severity=severity,
                label="Critical toxicity risk" if critical else "Low risk note",
                description="Computed risk flag.",
                metadata={"critical": critical} if critical else {},
            )
        ],
        metadata={"risk_level": risk},
    )


def _candidate(**overrides: Any) -> MoleculeCandidate:
    score = overrides.pop("score_breakdown", _score())
    literature = overrides.pop("literature_evidence", _literature_bundle())
    developability = overrides.pop("developability_assessment", _developability())
    payload: dict[str, Any] = {
        "name": "Candidate",
        "molecule_type": "small_molecule",
        "identifiers": {"chembl": "CHEMBL1"},
        "known_targets": ["MAOB"],
        "chemical_metadata": {"canonical_smiles": "CCO"},
        "evidence": [
            EvidenceItem(
                source="ChEMBL",
                source_record_id="mol-1",
                title="Molecule evidence",
                evidence_type="activity",
                summary="Molecule target evidence.",
                confidence=0.8,
            )
        ],
        "score": score.final_score,
        "score_breakdown": score,
        "literature_evidence": literature,
        "developability_assessment": developability,
        "warnings": [],
    }
    payload.update(overrides)
    return MoleculeCandidate(**payload)


def _run(
    *,
    candidates: list[MoleculeCandidate] | None = None,
    generated: list[GeneratedMoleculeHypothesis] | None = None,
) -> RankingRun:
    return RankingRun(
        disease=_disease(),
        targets=[_target()],
        candidates=[_candidate()] if candidates is None else candidates,
        generated_candidates=generated or [],
        traces=[AgentTrace(agent_name="test", input_summary="in", output_summary="out")],
    )


def test_high_scoring_existing_candidate_becomes_high_priority():
    workspace = build_review_workspace(_run(), config={"run_id": "run-high"})

    item = workspace.review_items[0]

    assert item.priority_bucket == "high_priority"
    assert item.evidence_summary["score_breakdown"]["final_score"] == 0.8
    assert item.evidence_summary["target_evidence_count"] == 1
    assert item.evidence_summary["molecule_evidence_count"] == 1
    assert item.evidence_summary["literature_claim_counts"]["supports"] == 1
    assert item.evidence_summary["safety_warning_count"] == 0
    assert item.evidence_summary["developability_risk_level"] == "low"


def test_generated_candidate_does_not_become_high_priority_by_default():
    generated = GeneratedMoleculeHypothesis(
        name="Generated-MAOB-001",
        canonical_smiles="CCN",
        target_symbol="MAOB",
        generation_score=0.93,
        min_seed_similarity=0.3,
        max_seed_similarity=0.6,
        mean_seed_similarity=0.45,
        developability_assessment=_developability(),
    )

    workspace = build_review_workspace(
        _run(candidates=[], generated=[generated]),
        config={"run_id": "run-generated"},
    )

    item = workspace.review_items[0]
    assert item.candidate_origin == "generated"
    assert item.priority_bucket == "medium_priority"
    assert item.evidence_summary["generated_score"] == 0.93


def test_critical_developability_risk_becomes_reject_suggested():
    candidate = _candidate(
        developability_assessment=_developability(risk="critical", critical=True),
    )

    workspace = build_review_workspace(_run(candidates=[candidate]), config={"run_id": "run-risk"})

    item = workspace.review_items[0]
    assert item.priority_bucket == "reject_suggested"
    assert "critical_developability_risk" in item.risk_flags


def test_contradictory_literature_becomes_needs_review():
    candidate = _candidate(literature_evidence=_literature_bundle(contradictory=True))

    workspace = build_review_workspace(
        _run(candidates=[candidate]),
        config={"run_id": "run-contradictory"},
    )

    item = workspace.review_items[0]
    assert item.priority_bucket == "needs_review"
    assert item.evidence_summary["literature_claim_counts"]["contradicts"] == 1


def test_missing_structure_policy_marks_needs_review_or_rejects_when_required():
    candidate = _candidate(
        chemical_metadata={},
        developability_assessment=_developability(structure_available=False),
    )

    soft_workspace = build_review_workspace(
        _run(candidates=[candidate]),
        config={"run_id": "run-soft", "require_structure_for_review": False},
    )
    strict_workspace = build_review_workspace(
        _run(candidates=[candidate]),
        config={"run_id": "run-strict", "require_structure_for_review": True},
    )

    assert soft_workspace.review_items[0].priority_bucket == "needs_review"
    assert "missing_structure" in soft_workspace.review_items[0].risk_flags
    assert strict_workspace.review_items[0].priority_bucket == "reject_suggested"
