from __future__ import annotations

from typing import Any

from molecule_ranker.generation.schemas import GenerationConfig
from molecule_ranker.generation.seed_selector import SeedSelector
from molecule_ranker.schemas import (
    Disease,
    EvidenceItem,
    LiteratureEvidenceBundle,
    MoleculeCandidate,
    ScoreBreakdown,
    Target,
)


def _disease() -> Disease:
    return Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        identifiers={"mondo": "MONDO:0005180"},
    )


def _target(symbol: str = "MAOB", score: float = 0.84) -> Target:
    return Target(
        symbol=symbol,
        name=f"{symbol} target",
        identifiers={"ensembl": f"ENSG_{symbol}"},
        disease_relevance_score=score,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id=f"MONDO:{symbol}",
                title="Disease target association",
                evidence_type="target_disease_association",
                summary="Retrieved target association.",
                confidence=score,
            )
        ],
    )


def _evidence(
    evidence_type: str,
    confidence: float = 0.8,
    *,
    source: str = "ChEMBL",
    record_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        source=source,
        source_record_id=record_id or f"{source}:{evidence_type}:{confidence}",
        title=f"{source} {evidence_type}",
        evidence_type=evidence_type,
        summary=f"Retrieved {evidence_type} evidence.",
        confidence=confidence,
        metadata=metadata or {},
    )


def _score(
    *,
    molecule_target_evidence: float = 0.8,
    disease_target_relevance: float = 0.8,
) -> ScoreBreakdown:
    return ScoreBreakdown(
        disease_target_relevance=disease_target_relevance,
        molecule_target_evidence=molecule_target_evidence,
        mechanism_plausibility=0.7,
        clinical_precedence=0.4,
        safety_prior=0.8,
        data_quality=0.8,
        novelty_or_repurposing_value=0.5,
        literature_quality=0.0,
        final_score=0.7,
        confidence=0.7,
        explanation="Seed test score.",
    )


def _candidate(
    name: str,
    *,
    smiles: str | None = "C#CCN(C)Cc1ccccc1",
    known_targets: list[str] | None = None,
    evidence: list[EvidenceItem] | None = None,
    score: ScoreBreakdown | None = None,
    literature_quality: float = 0.0,
    chemical_metadata: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> MoleculeCandidate:
    metadata = dict(chemical_metadata or {})
    if smiles is not None:
        metadata.setdefault("canonical_smiles", smiles)
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": f"CHEMBL_{name.upper()}"},
        known_targets=known_targets or ["MAOB"],
        chemical_metadata=metadata,
        evidence=evidence
        if evidence is not None
        else [
            _evidence("mechanism", 0.9),
            _evidence("activity", 0.85),
            _evidence("chemical_annotation", 0.75, source="PubChem"),
        ],
        score=score.final_score if score else None,
        score_breakdown=score,
        literature_evidence=LiteratureEvidenceBundle(
            candidate_name=name,
            query_count=1 if literature_quality else 0,
            quality_score=literature_quality,
        )
        if literature_quality
        else None,
        warnings=warnings or [],
    )


def test_selects_evidence_backed_structured_candidates():
    selector = SeedSelector()

    seeds = selector.select(
        disease=_disease(),
        targets=[_target()],
        candidates=[_candidate("Rasagiline")],
        literature_evidence=None,
        config=GenerationConfig(max_seed_molecules=3),
    )

    assert [seed.name for seed in seeds] == ["Rasagiline"]
    assert seeds[0].canonical_smiles == "C#CCN(C)Cc1ccccc1"
    assert seeds[0].evidence_count == 3
    assert seeds[0].best_evidence_confidence == 0.9
    assert seeds[0].target_relevance_score == 0.84
    assert "real ChEMBL molecule-target evidence" in seeds[0].seed_selection_reason
    assert selector.trace_metadata["selected_seeds"][0]["name"] == "Rasagiline"
    assert selector.trace_metadata["target_coverage"]["MAOB"] == ["Rasagiline"]


def test_rejects_candidates_without_structure():
    selector = SeedSelector()
    candidates = [_candidate("NoStructure", smiles=None, chemical_metadata={})]

    seeds = selector.select(
        disease=_disease(),
        targets=[_target()],
        candidates=candidates,
        literature_evidence=None,
        config=GenerationConfig(require_structure_for_seed=True),
    )

    assert seeds == []
    assert selector.trace_metadata["rejected_seed_candidates"][0]["name"] == "NoStructure"
    assert "missing_valid_structure" in selector.trace_metadata["rejected_seed_candidates"][0][
        "rejection_reasons"
    ]


def test_selects_candidates_with_convertible_inchi_structure():
    selector = SeedSelector()
    candidate = _candidate(
        "InChISeed",
        smiles=None,
        chemical_metadata={"inchi": "InChI=1S/C2H6O/c1-2-3/h3H,2H2,1H3"},
    )

    seeds = selector.select(
        disease=_disease(),
        targets=[_target()],
        candidates=[candidate],
        literature_evidence=None,
        config=GenerationConfig(require_structure_for_seed=True),
    )

    assert [seed.name for seed in seeds] == ["InChISeed"]
    assert seeds[0].canonical_smiles == "CCO"


def test_rejects_candidates_without_target_overlap():
    selector = SeedSelector()

    seeds = selector.select(
        disease=_disease(),
        targets=[_target("MAOB")],
        candidates=[_candidate("OffTarget", known_targets=["SNCA"])],
        literature_evidence=None,
        config=GenerationConfig(),
    )

    assert seeds == []
    rejected = selector.trace_metadata["rejected_seed_candidates"][0]
    assert rejected["name"] == "OffTarget"
    assert "no_evidence_backed_target_overlap" in rejected["rejection_reasons"]


def test_prioritizes_higher_evidence_candidates():
    selector = SeedSelector()
    low = _candidate(
        "LowEvidence",
        evidence=[_evidence("activity", 0.45)],
        score=_score(molecule_target_evidence=0.45, disease_target_relevance=0.6),
    )
    high = _candidate(
        "HighEvidence",
        smiles="NCC(O)c1ccc(OCc2ccccc2)cc1",
        evidence=[
            _evidence("mechanism", 0.95),
            _evidence("activity", 0.9),
            _evidence("chemical_annotation", 0.8, source="PubChem"),
        ],
        score=_score(molecule_target_evidence=0.95, disease_target_relevance=0.9),
        literature_quality=0.7,
    )

    seeds = selector.select(
        disease=_disease(),
        targets=[_target(score=0.9)],
        candidates=[low, high],
        literature_evidence=None,
        config=GenerationConfig(max_seed_molecules=2),
    )

    assert [seed.name for seed in seeds] == ["HighEvidence", "LowEvidence"]
    assert seeds[0].metadata["seed_score"] > seeds[1].metadata["seed_score"]
    assert seeds[0].metadata["literature_support_score"] == 0.7


def test_records_rejection_reasons_for_database_and_safety_exclusions():
    selector = SeedSelector()
    mention_only = _candidate(
        "MentionOnly",
        evidence=[
            _evidence(
                "literature_mention",
                0.4,
                source="PubMed",
                metadata={"support_level": "mentions"},
            )
        ],
        literature_quality=0.1,
    )
    serious_warning = _candidate(
        "SafetyExcluded",
        evidence=[
            _evidence("mechanism", 0.8),
            _evidence(
                "safety_warning",
                0.9,
                metadata={"warning_class": "boxed_warning", "warning_type": "Black Box Warning"},
            ),
        ],
    )

    seeds = selector.select(
        disease=_disease(),
        targets=[_target()],
        candidates=[mention_only, serious_warning],
        literature_evidence=None,
        config=GenerationConfig(exclude_seed_with_serious_warnings=True),
    )

    assert seeds == []
    rejected = {
        item["name"]: item["rejection_reasons"]
        for item in selector.trace_metadata["rejected_seed_candidates"]
    }
    assert "no_real_molecule_target_evidence" in rejected["MentionOnly"]
    assert "serious_safety_warning" in rejected["SafetyExcluded"]
