from __future__ import annotations

from typing import Any

from molecule_ranker.generation.objective_builder import GenerationObjectiveBuilder
from molecule_ranker.generation.schemas import GenerationConfig, SeedMolecule
from molecule_ranker.schemas import Disease, EvidenceItem, MoleculeCandidate, Target


def _disease() -> Disease:
    return Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        identifiers={"mondo": "MONDO:0005180"},
    )


def _target(
    symbol: str,
    *,
    score: float = 0.84,
    mechanism: str | None = None,
    evidence_backed: bool = True,
) -> Target:
    return Target(
        symbol=symbol,
        name=f"{symbol} target",
        identifiers={"ensembl": f"ENSG_{symbol}"},
        disease_relevance_score=score,
        mechanism=mechanism,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id=f"MONDO:{symbol}" if evidence_backed else None,
                title="Disease target association",
                evidence_type="target_disease_association",
                summary="Retrieved target association.",
                confidence=score,
            )
        ],
    )


def _seed(
    name: str,
    *,
    smiles: str,
    matched_targets: list[str],
    seed_id: str,
) -> SeedMolecule:
    return SeedMolecule(
        name=name,
        canonical_smiles=smiles,
        identifiers={"chembl": seed_id},
        known_targets=matched_targets,
        source_candidate_name=name,
        evidence_count=3,
        best_evidence_confidence=0.9,
        target_relevance_score=0.84,
        seed_selection_reason="Selected for objective-builder test.",
        metadata={"matched_targets": matched_targets, "seed_score": 0.8},
    )


def _candidate(
    name: str,
    *,
    known_targets: list[str],
    evidence: list[EvidenceItem] | None = None,
    metadata: dict[str, Any] | None = None,
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={"chembl": f"CHEMBL_{name.upper()}"},
        known_targets=known_targets,
        chemical_metadata=metadata or {"canonical_smiles": "C#CCN(C)Cc1ccccc1"},
        evidence=evidence or [],
    )


def _mechanism_evidence(target: str, summary: str = "ChEMBL reports MAO-B inhibition."):
    return EvidenceItem(
        source="ChEMBL",
        source_record_id=f"mechanism:{target}",
        title="Mechanism record",
        evidence_type="mechanism",
        summary=summary,
        confidence=0.9,
        metadata={"target_symbol": target, "mechanism": summary},
    )


def test_creates_objectives_from_seed_target_groups():
    builder = GenerationObjectiveBuilder()
    seeds = [
        _seed(
            "Rasagiline",
            smiles="C#CCN(C)Cc1ccccc1",
            matched_targets=["MAOB"],
            seed_id="CHEMBL887",
        ),
        _seed(
            "Safinamide",
            smiles="NCC(O)c1ccc(OCc2ccccc2)cc1",
            matched_targets=["MAOB"],
            seed_id="CHEMBL123",
        ),
    ]

    objectives = builder.build(
        disease=_disease(),
        targets=[_target("MAOB", mechanism="Retrieved MAOB modulation evidence.")],
        seeds=seeds,
        existing_candidates=[
            _candidate(
                "Rasagiline",
                known_targets=["MAOB"],
                evidence=[_mechanism_evidence("MAOB")],
            )
        ],
        literature_evidence=None,
        config=GenerationConfig(max_generation_objectives=2),
    )

    assert len(objectives) == 1
    objective = objectives[0]
    assert objective.objective_id == "parkinson-disease:MAOB"
    assert objective.disease_name == "Parkinson disease"
    assert objective.target_symbol == "MAOB"
    assert objective.seed_molecule_names == ["Rasagiline", "Safinamide"]
    assert objective.seed_molecule_ids == ["CHEMBL887", "CHEMBL123"]
    assert objective.objective_type == "target_conditioned_analog_generation"
    assert objective.target_identifiers == {"ensembl": "ENSG_MAOB"}
    assert objective.metadata["target_evidence_count"] == 1
    assert objective.metadata["seed_count"] == 2
    assert builder.trace_metadata["created_objectives"][0]["target_symbol"] == "MAOB"


def test_skips_targets_with_no_selected_seeds():
    builder = GenerationObjectiveBuilder()

    objectives = builder.build(
        disease=_disease(),
        targets=[_target("MAOB"), _target("SNCA", score=0.9)],
        seeds=[
            _seed(
                "Rasagiline",
                smiles="C#CCN(C)Cc1ccccc1",
                matched_targets=["MAOB"],
                seed_id="CHEMBL887",
            )
        ],
        existing_candidates=[],
        literature_evidence=None,
        config=GenerationConfig(max_generation_objectives=5),
    )

    assert [objective.target_symbol for objective in objectives] == ["MAOB"]
    skipped = builder.trace_metadata["skipped_targets"]
    assert {"target_symbol": "SNCA", "reason": "no_selected_seed_molecules"} in skipped


def test_does_not_invent_mechanism_hints():
    builder = GenerationObjectiveBuilder()

    objectives = builder.build(
        disease=_disease(),
        targets=[_target("MAOB", mechanism=None)],
        seeds=[
            _seed(
                "Rasagiline",
                smiles="C#CCN(C)Cc1ccccc1",
                matched_targets=["MAOB"],
                seed_id="CHEMBL887",
            )
        ],
        existing_candidates=[
            _candidate(
                "Rasagiline",
                known_targets=["MAOB"],
                evidence=[
                    EvidenceItem(
                        source="PubMed",
                        source_record_id="pmid-1",
                        title="Mention-only paper",
                        evidence_type="literature_mention",
                        summary="Rasagiline and Parkinson disease are mentioned.",
                        confidence=0.2,
                    )
                ],
            )
        ],
        literature_evidence=None,
        config=GenerationConfig(),
    )

    assert len(objectives) == 1
    assert objectives[0].mechanism_hint is None
    assert objectives[0].metadata["mechanism_hint_source"] is None


def test_stores_descriptor_derived_constraints():
    builder = GenerationObjectiveBuilder()
    seeds = [
        _seed(
            "Rasagiline",
            smiles="C#CCN(C)Cc1ccccc1",
            matched_targets=["MAOB"],
            seed_id="CHEMBL887",
        ),
        _seed(
            "Safinamide",
            smiles="NCC(O)c1ccc(OCc2ccccc2)cc1",
            matched_targets=["MAOB"],
            seed_id="CHEMBL123",
        ),
    ]

    objectives = builder.build(
        disease=_disease(),
        targets=[_target("MAOB")],
        seeds=seeds,
        existing_candidates=[],
        literature_evidence=None,
        config=GenerationConfig(seed_property_margin_fraction=0.10),
    )

    constraints = objectives[0].constraints
    assert set(constraints) >= {"molecular_weight", "logp", "tpsa"}
    assert constraints["molecular_weight"]["source"] == "seed_descriptor_distribution"
    assert constraints["molecular_weight"]["min"] < constraints["molecular_weight"]["seed_min"]
    assert constraints["molecular_weight"]["max"] > constraints["molecular_weight"]["seed_max"]
    assert constraints["logp"]["margin_fraction"] == 0.10
    assert objectives[0].metadata["seed_descriptor_summary"]["seed_count"] == 2
