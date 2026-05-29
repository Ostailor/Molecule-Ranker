from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from molecule_ranker.experiments.schemas import AssayContext, AssayEndpoint, AssayResult
from molecule_ranker.experiments.store import ExperimentalResultStore
from molecule_ranker.models.datasets import build_assay_model_training_dataset
from molecule_ranker.models.schemas import ModelEndpoint, ModelFeatureSpec

FIXED_TIME = datetime(2026, 2, 3, 4, 5, tzinfo=UTC)


def _model_endpoint(
    *,
    endpoint_name: str = "binding_affinity",
    label_type: str = "binary",
    endpoint_category: str = "potency",
    target_symbol: str | None = "MAOB",
    disease_name: str | None = "Parkinson disease",
) -> ModelEndpoint:
    return ModelEndpoint(
        endpoint_id=f"model-endpoint-{endpoint_name}",
        endpoint_name=endpoint_name,
        endpoint_category=endpoint_category,  # type: ignore[arg-type]
        target_symbol=target_symbol,
        disease_name=disease_name,
        assay_type="biochemical",
        unit="nM",
        label_type=label_type,  # type: ignore[arg-type]
        positive_label="positive" if label_type == "binary" else None,
        directionality="lower_is_better",
        thresholds={"active_nm": 100.0},
    )


def _feature_spec() -> ModelFeatureSpec:
    return ModelFeatureSpec(
        feature_spec_id="feature-spec-1",
        feature_families=["rdkit_descriptors", "target_context"],
        fingerprint_radius=None,
        fingerprint_bits=None,
        descriptor_names=["measured_value_numeric"],
        normalization="none",
    )


def _context(
    *,
    endpoint_name: str = "binding_affinity",
    endpoint_category: str = "potency",
    target_symbol: str | None = "MAOB",
    disease_name: str | None = "Parkinson disease",
) -> AssayContext:
    return AssayContext(
        assay_context_id=f"context-{endpoint_name}-{target_symbol or 'none'}",
        assay_name=f"{endpoint_name} assay",
        assay_type="biochemical",
        target_symbol=target_symbol,
        disease_name=disease_name,
        endpoint=AssayEndpoint(
            endpoint_id=f"endpoint-{endpoint_name}",
            name=endpoint_name,
            endpoint_category=endpoint_category,  # type: ignore[arg-type]
            unit="nM",
            directionality="lower_is_better",
        ),
    )


def _result(
    result_id: str,
    *,
    candidate_id: str | None = "candidate-1",
    candidate_name: str = "Candidate 1",
    candidate_origin: str = "existing",
    canonical_smiles: str | None = "CCO",
    inchi_key: str | None = "INCHI-1",
    outcome_label: str = "positive",
    activity_direction: str = "active",
    qc_status: str = "passed",
    normalized_value: float | None = 42.0,
    endpoint_name: str = "binding_affinity",
    endpoint_category: str = "potency",
    target_symbol: str | None = "MAOB",
    disease_name: str | None = "Parkinson disease",
) -> AssayResult:
    return AssayResult(
        result_id=result_id,
        run_id="run-1",
        candidate_id=candidate_id,
        candidate_name=candidate_name,
        candidate_origin=candidate_origin,  # type: ignore[arg-type]
        canonical_smiles=canonical_smiles,
        inchi_key=inchi_key,
        disease_name=disease_name,
        target_symbol=target_symbol,
        assay_context=_context(
            endpoint_name=endpoint_name,
            endpoint_category=endpoint_category,
            target_symbol=target_symbol,
            disease_name=disease_name,
        ),
        measured_value=normalized_value,
        measured_value_numeric=normalized_value,
        unit="nM",
        normalized_value=normalized_value,
        normalized_unit="nM",
        outcome_label=outcome_label,  # type: ignore[arg-type]
        activity_direction=activity_direction,  # type: ignore[arg-type]
        confidence=0.8,
        qc_status=qc_status,  # type: ignore[arg-type]
        result_date=date(2026, 2, 3),
        source="csv_import",
        source_record_id=f"row-{result_id}",
        imported_at=FIXED_TIME,
    )


def test_builds_binary_endpoint_dataset(tmp_path: Path) -> None:
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results(
        [
            _result("result-1", candidate_id="candidate-1", outcome_label="positive"),
            _result(
                "result-2",
                candidate_id="candidate-2",
                candidate_name="Candidate 2",
                outcome_label="negative",
                activity_direction="inactive",
            ),
        ]
    )

    built = build_assay_model_training_dataset(
        store,
        candidates=[
            {"candidate_id": "candidate-1", "candidate_name": "Candidate 1"},
            {"candidate_id": "candidate-2", "candidate_name": "Candidate 2"},
        ],
        generated_molecules=[],
        endpoint=_model_endpoint(),
        feature_spec=_feature_spec(),
        output_dir=tmp_path / "model-artifacts",
        config={},
    )

    assert built.dataset.row_count == 2
    assert built.dataset.positive_count == 1
    assert built.dataset.negative_count == 1
    assert built.labels == [1, 0]
    assert json.loads(built.labels_path.read_text())["labels"] == [1, 0]
    assert built.dataset.feature_matrix_uri == str(built.feature_matrix_path)
    assert built.dataset.labels_uri == str(built.labels_path)


def test_builds_regression_endpoint_dataset(tmp_path: Path) -> None:
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results(
        [
            _result("result-1", candidate_id="candidate-1", normalized_value=12.5),
            _result(
                "result-2",
                candidate_id="candidate-2",
                candidate_name="Candidate 2",
                normalized_value=80.0,
            ),
        ]
    )

    built = build_assay_model_training_dataset(
        store,
        candidates=[
            {"candidate_id": "candidate-1", "candidate_name": "Candidate 1"},
            {"candidate_id": "candidate-2", "candidate_name": "Candidate 2"},
        ],
        generated_molecules=[],
        endpoint=_model_endpoint(label_type="regression"),
        feature_spec=_feature_spec(),
        output_dir=tmp_path / "model-artifacts",
        config={},
    )

    assert built.dataset.row_count == 2
    assert built.labels == [12.5, 80.0]
    assert built.dataset.positive_count is None
    assert built.dataset.negative_count is None


def test_failed_qc_and_inconclusive_are_excluded_by_default(tmp_path: Path) -> None:
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results(
        [
            _result("result-1", candidate_id="candidate-1", outcome_label="positive"),
            _result("result-2", candidate_id="candidate-2", qc_status="failed"),
            _result("result-3", candidate_id="candidate-3", outcome_label="inconclusive"),
        ]
    )

    built = build_assay_model_training_dataset(
        store,
        candidates=[{"candidate_id": "candidate-1", "candidate_name": "Candidate 1"}],
        generated_molecules=[],
        endpoint=_model_endpoint(),
        feature_spec=_feature_spec(),
        output_dir=tmp_path / "model-artifacts",
        config={},
    )

    assert built.dataset.row_count == 1
    assert set(built.dataset.excluded_result_ids) == {"result-2", "result-3"}
    assert built.dataset.exclusion_reasons["result-2"] == "qc_status_failed"
    assert built.dataset.exclusion_reasons["result-3"] == "inconclusive_excluded"


def test_generated_analog_is_not_labeled_from_seed_result(tmp_path: Path) -> None:
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results(
        [
            _result(
                "seed-result",
                candidate_id="seed-1",
                candidate_name="Seed Molecule",
                candidate_origin="existing",
                canonical_smiles="CCO",
            )
        ]
    )

    built = build_assay_model_training_dataset(
        store,
        candidates=[],
        generated_molecules=[
            {
                "generated_id": "generated-1",
                "name": "Generated Analog",
                "canonical_smiles": "CCN",
                "parent_seed_ids": ["seed-1"],
            }
        ],
        endpoint=_model_endpoint(),
        feature_spec=_feature_spec(),
        output_dir=tmp_path / "model-artifacts",
        config={},
    )

    assert built.dataset.row_count == 0
    assert built.dataset.excluded_result_ids == ["seed-result"]
    assert built.dataset.exclusion_reasons["seed-result"] == "candidate_not_linked"


def test_endpoint_mismatch_excluded_unless_pooling_is_explicit_and_labeled(tmp_path: Path) -> None:
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results(
        [
            _result("result-1", candidate_id="candidate-1", endpoint_name="binding_affinity"),
            _result("result-2", candidate_id="candidate-2", endpoint_name="selectivity"),
        ]
    )

    default_built = build_assay_model_training_dataset(
        store,
        candidates=[
            {"candidate_id": "candidate-1", "candidate_name": "Candidate 1"},
            {"candidate_id": "candidate-2", "candidate_name": "Candidate 2"},
        ],
        generated_molecules=[],
        endpoint=_model_endpoint(endpoint_name="binding_affinity"),
        feature_spec=_feature_spec(),
        output_dir=tmp_path / "default",
        config={},
    )
    pooled = build_assay_model_training_dataset(
        store,
        candidates=[
            {"candidate_id": "candidate-1", "candidate_name": "Candidate 1"},
            {"candidate_id": "candidate-2", "candidate_name": "Candidate 2"},
        ],
        generated_molecules=[],
        endpoint=_model_endpoint(endpoint_name="binding_affinity"),
        feature_spec=_feature_spec(),
        output_dir=tmp_path / "pooled",
        config={"allow_endpoint_pooling": True, "pooled_endpoint_label": "binding_or_selectivity"},
    )

    assert default_built.dataset.row_count == 1
    assert default_built.dataset.exclusion_reasons["result-2"] == "endpoint_mismatch"
    assert pooled.dataset.row_count == 2
    assert pooled.dataset.metadata["pooled_endpoint_label"] == "binding_or_selectivity"


def test_dataset_manifest_preserves_provenance(tmp_path: Path) -> None:
    store = ExperimentalResultStore(tmp_path / "experiments.sqlite")
    store.import_results([_result("result-1", candidate_id="candidate-1")])

    built = build_assay_model_training_dataset(
        store,
        candidates=[{"candidate_id": "candidate-1", "candidate_name": "Candidate 1"}],
        generated_molecules=[],
        endpoint=_model_endpoint(),
        feature_spec=_feature_spec(),
        output_dir=tmp_path / "model-artifacts",
        config={},
    )

    manifest = json.loads(built.manifest_path.read_text())

    assert manifest["dataset_id"] == built.dataset.dataset_id
    assert manifest["source_result_ids"] == ["result-1"]
    assert manifest["included_candidate_ids"] == ["candidate-1"]
    assert manifest["feature_matrix_uri"] == str(built.feature_matrix_path)
    assert manifest["labels_uri"] == str(built.labels_path)
