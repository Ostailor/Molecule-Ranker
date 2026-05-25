from __future__ import annotations

import json

import pytest

from molecule_ranker.generation.benchmark import (
    GenerationBenchmarkError,
    benchmark_generated_file,
)


def _generated(
    generated_id: str,
    smiles: str,
    *,
    novelty_class: str = "novel_analog",
    seed_similarity: float = 0.6,
    existing_similarity: float = 0.2,
    diversity_cluster: str = "cluster-1",
):
    return {
        "generated_id": generated_id,
        "smiles": smiles,
        "canonical_smiles": smiles,
        "inchi_key": f"{generated_id}-KEY",
        "conditioned_targets": ["MAOB"],
        "descriptors": {
            "molecular_weight": 180.0,
            "logp": 2.1,
            "heavy_atom_count": 12,
        },
        "validation": {
            "valid_rdkit_mol": True,
            "rejection_reasons": [],
        },
        "novelty": {
            "novelty_class": novelty_class,
            "max_similarity_to_seed": seed_similarity,
            "max_similarity_to_existing": existing_similarity,
        },
        "diversity_cluster": diversity_cluster,
    }


def test_benchmark_computes_generation_metrics(tmp_path):
    retained = [
        _generated("gen-1", "CCOc1ccccc1", diversity_cluster="cluster-1"),
        _generated("gen-2", "CCN(CC)CC", diversity_cluster="cluster-2"),
    ]
    rejected_duplicate = _generated(
        "gen-duplicate",
        "CCOc1ccccc1",
        novelty_class="near_duplicate",
        seed_similarity=0.94,
        existing_similarity=0.91,
    )
    payload = {
        "success": True,
        "generation_enabled": True,
        "generated_count": 4,
        "retained_count": 2,
        "rejected_count": 1,
        "retained_generated_molecules": retained,
        "rejected_generated_molecules": [
            {
                "generated_molecule": rejected_duplicate,
                "rejection_reasons": ["near_duplicate"],
            }
        ],
    }
    path = tmp_path / "generated_candidates.json"
    path.write_text(json.dumps(payload))

    result = benchmark_generated_file(path)

    assert result.validity_rate == 0.75
    assert result.uniqueness_rate == 1.0
    assert result.novelty_rate == pytest.approx(2 / 3, abs=0.001)
    assert result.near_duplicate_rate == pytest.approx(1 / 3, abs=0.001)
    assert result.retained_rate == 0.5
    assert result.average_similarity_to_seed == pytest.approx(0.713, abs=0.001)
    assert result.average_similarity_to_existing == pytest.approx(0.437, abs=0.001)
    assert result.descriptor_distribution_summary["molecular_weight"].mean == 180.0
    assert result.target_coverage == {"MAOB": 2}
    assert result.diversity_cluster_count == 2


def test_benchmark_handles_empty_generated_list(tmp_path):
    path = tmp_path / "generated_candidates.json"
    path.write_text(
        json.dumps(
            {
                "success": True,
                "generation_enabled": True,
                "generated_count": 0,
                "retained_count": 0,
                "rejected_count": 0,
                "retained_generated_molecules": [],
                "rejected_generated_molecules": [],
            }
        )
    )

    result = benchmark_generated_file(path)

    assert result.generated_count == 0
    assert result.validity_rate == 0.0
    assert result.target_coverage == {}


def test_benchmark_rejects_malformed_generated_file(tmp_path):
    path = tmp_path / "generated_candidates.json"
    path.write_text(json.dumps({"success": True, "generated_count": 2}))

    with pytest.raises(GenerationBenchmarkError):
        benchmark_generated_file(path)
