from __future__ import annotations

import pytest
from pydantic import ValidationError

from molecule_ranker.design import DesignObjectiveBuilderV2, DesignObjectiveV2
from molecule_ranker.generation.schemas import SeedMolecule
from molecule_ranker.schemas import Disease, EvidenceItem, Target


def _disease() -> Disease:
    return Disease(
        input_name="Parkinson disease",
        canonical_name="Parkinson disease",
        identifiers={"mondo": "MONDO:0005180"},
    )


def _target(
    symbol: str = "MAOB",
    *,
    mechanism: str | None = "MAOB inhibitor",
    evidence_backed: bool = True,
) -> Target:
    return Target(
        symbol=symbol,
        name=f"{symbol} target",
        identifiers={"ensembl": f"ENSG_{symbol}"},
        disease_relevance_score=0.84,
        mechanism=mechanism,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id=f"OT:{symbol}" if evidence_backed else None,
                title="Disease target association",
                evidence_type="target_disease_association",
                summary="Retrieved disease target association.",
                confidence=0.84,
            )
        ],
    )


def _seed(name: str = "Rasagiline", target: str = "MAOB") -> SeedMolecule:
    return SeedMolecule(
        name=name,
        canonical_smiles="C#CCN(C)Cc1ccccc1",
        identifiers={"chembl": f"CHEMBL_{name.upper()}"},
        known_targets=[target],
        source_candidate_name=name,
        evidence_count=3,
        best_evidence_confidence=0.9,
        target_relevance_score=0.84,
        seed_selection_reason="Evidence-backed seed.",
        metadata={
            "matched_targets": [target],
            "seed_score": 0.82,
            "scaffold_id": f"scaffold-{name.lower()}",
        },
    )


def test_builds_objective_from_target_and_seeds() -> None:
    objectives = DesignObjectiveBuilderV2().build(
        disease=_disease(),
        targets=[_target()],
        seeds=[_seed()],
        literature_evidence=None,
        review_decisions=[],
        max_objectives=3,
    )

    assert len(objectives) == 1
    objective = objectives[0]
    assert objective.objective_id == "parkinson-disease:MAOB:v2"
    assert objective.disease_name == "Parkinson disease"
    assert objective.target_symbol == "MAOB"
    assert objective.target_identifiers == {"ensembl": "ENSG_MAOB"}
    assert objective.desired_modality == "small_molecule"
    assert objective.seed_ids == ["CHEMBL_RASAGILINE"]
    assert objective.scaffold_ids == ["scaffold-rasagiline"]
    assert objective.hard_constraints["valid_molecule"] is True
    assert objective.hard_constraints["generated_label"] == "generated"
    assert "novelty" in objective.soft_constraints


def test_mechanism_action_only_if_source_exists() -> None:
    objectives = DesignObjectiveBuilderV2().build(
        disease=_disease(),
        targets=[_target(mechanism="retrieved inhibitor mechanism")],
        seeds=[_seed()],
        literature_evidence=None,
        review_decisions=[],
    )

    assert objectives[0].desired_action == "inhibitor"
    assert objectives[0].action_source == "retrieved_mechanism"
    assert objectives[0].evidence_context["action_evidence_source"] == "target.mechanism"


def test_unknown_mechanism_handled() -> None:
    objectives = DesignObjectiveBuilderV2().build(
        disease=_disease(),
        targets=[_target(mechanism=None)],
        seeds=[_seed()],
        literature_evidence=None,
        review_decisions=[],
    )

    assert objectives[0].desired_action == "unknown"
    assert objectives[0].action_source == "unknown"


def test_no_invented_action_from_target_metadata() -> None:
    target = _target(mechanism=None)
    target.metadata["desired_action"] = "inhibitor"

    objectives = DesignObjectiveBuilderV2().build(
        disease=_disease(),
        targets=[target],
        seeds=[_seed()],
        literature_evidence=None,
        review_decisions=[],
    )

    assert objectives[0].desired_action == "unknown"
    assert objectives[0].action_source == "unknown"


def test_constraints_validated() -> None:
    with pytest.raises(ValidationError):
        DesignObjectiveV2(
            objective_id="bad",
            disease_name="Parkinson disease",
            target_symbol="MAOB",
            target_identifiers={},
            desired_modality="small_molecule",
            desired_action="inhibitor",
            action_source="retrieved_mechanism",
            seed_ids=[],
            scaffold_ids=[],
            optimization_goals=[],
            hard_constraints={"valid_molecule": True},
            soft_constraints={},
            forbidden_patterns=[],
            target_context={},
            evidence_context={},
            uncertainty_context={},
            metadata={},
        )
