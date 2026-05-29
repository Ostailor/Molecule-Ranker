from __future__ import annotations

from molecule_ranker.models.applicability import assess_applicability_domain


def _training_rows() -> list[dict[str, object]]:
    return [
        {
            "canonical_smiles": "Cc1ccccc1",
            "target_symbol": "MAOB",
            "disease_name": "Parkinson disease",
        },
        {
            "canonical_smiles": "Oc1ccccc1",
            "target_symbol": "MAOB",
            "disease_name": "Parkinson disease",
        },
    ]


def test_close_analog_in_domain() -> None:
    result = assess_applicability_domain(
        candidate={
            "canonical_smiles": "Nc1ccccc1",
            "target_symbol": "MAOB",
            "disease_name": "Parkinson disease",
            "candidate_origin": "generated",
        },
        training_rows=_training_rows(),
        endpoint_context={"target_symbol": "MAOB", "disease_name": "Parkinson disease"},
        config={"in_domain_tanimoto": 0.35},
    )

    assert result.applicability_domain == "in_domain"
    assert result.confidence <= 1.0
    assert result.metrics["nearest_neighbor_tanimoto"] >= 0.35


def test_distant_molecule_out_of_domain() -> None:
    result = assess_applicability_domain(
        candidate={
            "canonical_smiles": "CCCCCCCCCCCC",
            "target_symbol": "MAOB",
            "disease_name": "Parkinson disease",
        },
        training_rows=_training_rows(),
        endpoint_context={"target_symbol": "MAOB", "disease_name": "Parkinson disease"},
        config={"near_domain_tanimoto": 0.25},
    )

    assert result.applicability_domain == "out_of_domain"
    assert result.confidence <= 0.35


def test_unseen_scaffold_near_or_out_depending_threshold() -> None:
    near = assess_applicability_domain(
        candidate={
            "canonical_smiles": "c1ccncc1",
            "target_symbol": "MAOB",
            "disease_name": "Parkinson disease",
        },
        training_rows=_training_rows(),
        endpoint_context={"target_symbol": "MAOB", "disease_name": "Parkinson disease"},
        config={
            "in_domain_tanimoto": 0.9,
            "near_domain_tanimoto": 0.15,
            "near_domain_descriptor_z": 20.0,
        },
    )
    out = assess_applicability_domain(
        candidate={
            "canonical_smiles": "c1ccncc1",
            "target_symbol": "MAOB",
            "disease_name": "Parkinson disease",
        },
        training_rows=_training_rows(),
        endpoint_context={"target_symbol": "MAOB", "disease_name": "Parkinson disease"},
        config={"in_domain_tanimoto": 0.9, "near_domain_tanimoto": 0.95},
    )

    assert near.metrics["training_scaffold_seen"] is False
    assert near.applicability_domain == "near_domain"
    assert out.applicability_domain == "out_of_domain"


def test_endpoint_mismatch_out_of_domain() -> None:
    result = assess_applicability_domain(
        candidate={
            "canonical_smiles": "Nc1ccccc1",
            "target_symbol": "EGFR",
            "disease_name": "Lung cancer",
        },
        training_rows=_training_rows(),
        endpoint_context={"target_symbol": "MAOB", "disease_name": "Parkinson disease"},
    )

    assert result.applicability_domain == "out_of_domain"
    assert "endpoint_mismatch" in result.warnings
