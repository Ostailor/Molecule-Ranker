from __future__ import annotations

from molecule_ranker.design.seed_scaffold_selector import DesignSeedScaffoldSelector
from molecule_ranker.schemas import (
    DevelopabilityAssessment,
    EvidenceItem,
    MoleculeCandidate,
    Target,
)


def _target(symbol: str) -> Target:
    return Target(
        symbol=symbol,
        name=f"{symbol} target",
        identifiers={"ensembl": f"ENSG_{symbol}"},
        disease_relevance_score=0.85,
        evidence=[
            EvidenceItem(
                source="Open Targets",
                source_record_id=f"OT:{symbol}",
                title="Disease target association",
                evidence_type="target_disease_association",
                summary="Retrieved disease-target association.",
                confidence=0.85,
            )
        ],
    )


def _candidate(
    name: str,
    smiles: str,
    target: str,
    *,
    evidence_confidence: float = 0.9,
    developability_score: float = 0.75,
    triage: str = "favorable_hypothesis",
) -> MoleculeCandidate:
    return MoleculeCandidate(
        name=name,
        molecule_type="small_molecule",
        identifiers={
            "chembl": f"CHEMBL_{name.upper().replace(' ', '_')}",
            "inchikey": f"INCHIKEY-{name.upper().replace(' ', '-')}",
        },
        known_targets=[target],
        chemical_metadata={"canonical_smiles": smiles, "inchikey": f"IK-{name}"},
        evidence=[
            EvidenceItem(
                source="ChEMBL",
                source_record_id=f"CHEMBL_MECH_{name}",
                title=f"{name} target mechanism",
                evidence_type="mechanism",
                summary=f"Retrieved molecule-target record for {target}.",
                confidence=evidence_confidence,
                metadata={"target_symbol": target},
            ),
            EvidenceItem(
                source="PubChem",
                source_record_id=f"CID-{name}",
                title=f"{name} structure",
                evidence_type="chemical_annotation",
                summary="Retrieved exact structure metadata.",
                confidence=0.8,
            ),
        ],
        developability_assessment=DevelopabilityAssessment(
            molecule_name=name,
            origin="existing",
            structure_available=True,
            canonical_smiles=smiles,
            developability_score=developability_score,
            triage_recommendation=triage,  # type: ignore[arg-type]
        ),
        score=evidence_confidence,
    )


def test_scaffold_extraction_works() -> None:
    selector = DesignSeedScaffoldSelector()

    scaffold = selector.extract_murcko_scaffold("COc1ccc(CCN)cc1")

    assert scaffold.scaffold_smiles == "c1ccccc1"
    assert scaffold.scaffold_type == "murcko"
    assert scaffold.source_seed_ids == []


def test_diverse_seed_set_selected_and_target_coverage_preserved() -> None:
    selector = DesignSeedScaffoldSelector()

    result = selector.select(
        targets=[_target("MAOB"), _target("SNCA")],
        candidates=[
            _candidate("Seed A", "COc1ccc(CCN)cc1", "MAOB", evidence_confidence=0.95),
            _candidate("Seed B", "CCN1CCN(c2ccccc2)CC1", "MAOB", evidence_confidence=0.88),
            _candidate("Seed C", "CN1CCC[C@H]1c1cccnc1", "SNCA", evidence_confidence=0.82),
        ],
        max_seeds_per_target=2,
    )

    selected_by_name = {seed.name: seed for seed in result.seeds}
    assert {"Seed A", "Seed B", "Seed C"} <= set(selected_by_name)
    assert set(result.target_coverage) == {"MAOB", "SNCA"}
    assert len(result.target_coverage["MAOB"]) == 2

    maob_scaffold_ids = {
        selected_by_name[seed_name].metadata["scaffold_id"]
        for seed_name in result.target_coverage["MAOB"]
    }
    assert len(maob_scaffold_ids) == 2


def test_critical_risk_seed_excluded() -> None:
    selector = DesignSeedScaffoldSelector()

    result = selector.select(
        targets=[_target("MAOB")],
        candidates=[
            _candidate("Good Seed", "COc1ccc(CCN)cc1", "MAOB"),
            _candidate(
                "Critical Seed",
                "CCN1CCN(c2ccccc2)CC1",
                "MAOB",
                developability_score=0.12,
                triage="high_risk_flags",
            ),
        ],
    )

    assert [seed.name for seed in result.seeds] == ["Good Seed"]
    assert result.rejected_candidates[0]["name"] == "Critical Seed"
    assert "critical_developability_risk" in result.rejected_candidates[0]["rejection_reasons"]


def test_serious_safety_warning_seed_excluded_unless_allowed() -> None:
    selector = DesignSeedScaffoldSelector()
    warning_seed = _candidate("Warning Seed", "COc1ccc(CCN)cc1", "MAOB")
    warning_seed.evidence.append(
        EvidenceItem(
            source="FDA",
            source_record_id="FDA-WARNING-1",
            title="Boxed warning",
            evidence_type="warning",
            summary="Imported serious safety warning.",
            confidence=0.9,
            metadata={"warning_type": "boxed warning"},
        )
    )

    rejected = selector.select(targets=[_target("MAOB")], candidates=[warning_seed])

    assert rejected.seeds == []
    assert rejected.rejected_candidates[0]["name"] == "Warning Seed"
    assert "serious_safety_warning" in rejected.rejected_candidates[0]["rejection_reasons"]

    allowed = selector.select(
        targets=[_target("MAOB")],
        candidates=[warning_seed],
        allow_serious_safety_warnings=True,
    )

    assert [seed.name for seed in allowed.seeds] == ["Warning Seed"]
    assert allowed.seeds[0].rejection_risks == ["serious_safety_warning_allowed"]


def test_selection_reasons_stored() -> None:
    selector = DesignSeedScaffoldSelector()

    result = selector.select(
        targets=[_target("MAOB")],
        candidates=[_candidate("Reasoned Seed", "COc1ccc(CCN)cc1", "MAOB")],
    )

    seed = result.seeds[0]
    scaffold = result.scaffolds[0]
    assert "direct molecule-target evidence" in seed.reason_selected
    assert "exact structure" in seed.reason_selected
    assert seed.rejection_risks == []
    assert scaffold.reason_selected
