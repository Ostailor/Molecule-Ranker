from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from molecule_ranker.models.registry import ModelRegistry
from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEndpoint,
    ModelFeatureSpec,
    ModelPrediction,
    ModelTrainingRun,
)


def _endpoint() -> ModelEndpoint:
    return ModelEndpoint(
        endpoint_id="endpoint-binary",
        endpoint_name="binary_endpoint",
        endpoint_category="potency",
        target_symbol="MAOB",
        disease_name="Parkinson disease",
        assay_type="biochemical",
        unit=None,
        label_type="binary",
        positive_label="positive",
        directionality="binary",
    )


def _feature_spec() -> ModelFeatureSpec:
    return ModelFeatureSpec(
        feature_spec_id="feature-spec-1",
        feature_families=["rdkit_descriptors"],
        fingerprint_radius=None,
        fingerprint_bits=None,
        descriptor_names=["molecular_weight"],
        normalization="none",
    )


def _model_card(model_id: str = "model-1") -> ModelCard:
    return ModelCard(
        model_id=model_id,
        model_name="binary local surrogate",
        model_version="1.2.0",
        plugin_name="local_sklearn_baseline",
        endpoint=_endpoint(),
        feature_spec=_feature_spec(),
        training_dataset_id="dataset-1",
        training_data_summary={"source_result_ids": ["result-1", "result-2"]},
        model_type="LogisticRegression",
        intended_use="Assay-specific prioritization only.",
        limitations=["Predictions are not experimental evidence."],
        metrics={"accuracy": 1.0},
        calibration_metrics={"status": "uncalibrated"},
        applicability_domain_method="feature_space",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
        metadata={"api_key": "sk-test-secret-value"},
    )


def _prediction() -> ModelPrediction:
    return ModelPrediction(
        prediction_id="prediction-1",
        model_id="model-1",
        model_version="1.2.0",
        endpoint_id="endpoint-binary",
        candidate_id="candidate-1",
        candidate_name="Candidate 1",
        candidate_origin="generated",
        canonical_smiles="CCO",
        inchi_key=None,
        predicted_value=True,
        predicted_probability=0.6,
        prediction_label="surrogate_positive",
        uncertainty=0.4,
        confidence=0.6,
        applicability_domain="near_domain",
        calibration_status="uncalibrated",
        explanation="Prediction artifact only.",
        warnings=["not evidence"],
        created_at=datetime(2026, 1, 4, tzinfo=UTC),
        metadata={"not_experimental_evidence": True},
    )


def test_register_and_list_model(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    registry.register_model_card(_model_card(), actor="tester")
    cards = registry.list_models()

    assert [card.model_id for card in cards] == ["model-1"]
    assert registry.get_model_card("model-1").model_name == "binary local surrogate"
    assert registry.audit_events()[-1]["event_type"] == "model_registered"


def test_save_and_load_predictions(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_model_card(_model_card())

    path = registry.save_prediction_batch("model-1", "batch-1", [_prediction()])
    loaded = registry.load_prediction_batch("batch-1")

    assert path.exists()
    assert len(loaded) == 1
    assert loaded[0].prediction_id == "prediction-1"
    assert loaded[0].metadata["not_experimental_evidence"] is True


def test_deactivate_model(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_model_card(_model_card(), actor="tester")

    registry.deactivate_model("model-1", reason="superseded", actor="tester")

    assert registry.list_models() == []
    assert registry.list_models(active_only=False)[0].metadata["registry_active"] is False
    assert registry.audit_events()[-1]["event_type"] == "model_deactivated"


def test_export_and_import_package(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_model_card(_model_card())
    run = ModelTrainingRun(
        training_run_id="run-1",
        model_id="model-1",
        dataset_id="dataset-1",
        status="succeeded",
        started_at=datetime(2026, 1, 4, tzinfo=UTC),
        completed_at=datetime(2026, 1, 4, 1, tzinfo=UTC),
        metrics={"accuracy": 1.0},
        calibration_metrics={"status": "uncalibrated"},
        artifact_paths={"model_card": "model_cards/model-1.json"},
    )
    registry.register_training_run(run)
    registry.save_prediction_batch("model-1", "batch-1", [_prediction()])

    package_path = registry.export_model_package("model-1", tmp_path / "model-package.zip")
    imported = ModelRegistry(
        db_path=tmp_path / "imported.sqlite",
        artifact_dir=tmp_path / "imported-artifacts",
    )
    imported.import_model_package(package_path)

    assert imported.get_model_card("model-1").training_data_summary["source_result_ids"] == [
        "result-1",
        "result-2",
    ]
    assert imported.load_prediction_batch("batch-1")[0].prediction_id == "prediction-1"


def test_no_secrets_in_model_package(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    registry.register_model_card(_model_card())

    package_path = registry.export_model_package("model-1", tmp_path / "model-package.zip")

    with zipfile.ZipFile(package_path) as archive:
        package_text = "\n".join(
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.endswith(".json")
        )
    assert "sk-test-secret-value" not in package_text
    assert json.loads(archive_json(package_path, "model_cards/model-1.json"))["metadata"][
        "api_key"
    ] == "[REDACTED]"


def archive_json(package_path: Path, name: str) -> str:
    with zipfile.ZipFile(package_path) as archive:
        return archive.read(name).decode("utf-8")


def _registry(tmp_path: Path) -> ModelRegistry:
    return ModelRegistry(
        db_path=tmp_path / "models.sqlite",
        artifact_dir=tmp_path / "artifacts",
    )
