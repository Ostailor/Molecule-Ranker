from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from molecule_ranker.agents.base import AgentExecutionError, PipelineContext
from molecule_ranker.agents.predictive_model import PredictiveModelAgent
from molecule_ranker.models.schemas import (
    ModelCard,
    ModelEndpoint,
    ModelFeatureSpec,
    ModelPrediction,
)
from molecule_ranker.schemas import EvidenceItem, GeneratedMoleculeHypothesis, MoleculeCandidate


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


def _model_card() -> ModelCard:
    return ModelCard(
        model_id="model-1",
        model_name="binary local surrogate",
        model_version="1.2.0",
        plugin_name="mock_predictor",
        endpoint=_endpoint(),
        feature_spec=_feature_spec(),
        training_dataset_id="dataset-1",
        training_data_summary={"row_count": 8},
        model_type="MockClassifier",
        intended_use="Assay-specific prioritization only.",
        limitations=["Predictions are not experimental evidence."],
        metrics={},
        calibration_metrics={"status": "uncalibrated"},
        applicability_domain_method="feature_space",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )


def _candidate() -> MoleculeCandidate:
    return MoleculeCandidate(
        name="Rasagiline",
        molecule_type="small_molecule",
        identifiers={"chembl": "CHEMBL887"},
        known_targets=["MAOB"],
        chemical_metadata={
            "canonical_smiles": "C#CCN1CCC2=CC=CC=C21",
            "inchi_key": "RUYUTDCTDCBNSZ-UHFFFAOYSA-N",
        },
    )


def _generated() -> GeneratedMoleculeHypothesis:
    return GeneratedMoleculeHypothesis(
        name="Generated-MAOB-001",
        canonical_smiles="C#CCN(C)CCc1ccccn1",
        target_symbol="MAOB",
        generation_score=0.7,
        min_seed_similarity=0.4,
        max_seed_similarity=0.6,
        mean_seed_similarity=0.5,
    )


def test_predictive_model_agent_disabled_noop(tmp_path: Path) -> None:
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        output_dir=tmp_path,
        config={"enable_predictive_models": False},
    )

    updated = PredictiveModelAgent(registry=_Registry([]), predictor=_Predictor()).run(context)

    assert "model_predictions" not in updated.config
    assert "model_predictions" not in updated.candidates[0].chemical_metadata
    assert updated.traces[-1].agent_name == "PredictiveModelAgent"
    assert updated.traces[-1].metadata["enabled"] is False


def test_predictive_model_agent_enabled_mocked_model_predicts(tmp_path: Path) -> None:
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        generated_candidates=[_generated()],
        output_dir=tmp_path,
        config={"enable_predictive_models": True},
    )

    updated = PredictiveModelAgent(
        registry=_Registry([_model_card()]),
        predictor=_Predictor(),
    ).run(context)

    assert len(updated.config["model_predictions"]) == 2
    assert updated.candidates[0].chemical_metadata["model_predictions"][0]["model_id"] == "model-1"
    assert updated.generated_candidates[0].trace["model_predictions"][0]["endpoint_id"] == (
        "endpoint-binary"
    )
    assert (tmp_path / "model_predictions.json").exists()
    assert updated.traces[-1].metadata["prediction_count"] == 2


def test_predictive_model_prediction_not_evidence_item(tmp_path: Path) -> None:
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        output_dir=tmp_path,
        config={"enable_predictive_models": True},
    )

    updated = PredictiveModelAgent(
        registry=_Registry([_model_card()]),
        predictor=_Predictor(),
    ).run(context)

    assert updated.candidates[0].evidence == []
    assert not isinstance(updated.config["model_predictions"][0], EvidenceItem)


def test_predictive_model_out_of_domain_warning(tmp_path: Path) -> None:
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        output_dir=tmp_path,
        config={"enable_predictive_models": True},
    )

    updated = PredictiveModelAgent(
        registry=_Registry([_model_card()]),
        predictor=_Predictor(applicability_domain="out_of_domain"),
    ).run(context)

    assert updated.traces[-1].metadata["out_of_domain_count"] == 1
    assert "out_of_domain_prediction" in updated.config["PredictiveModelAgent.warnings"]


def test_predictive_model_strict_failure_behavior(tmp_path: Path) -> None:
    context = PipelineContext(
        disease_input="Parkinson disease",
        candidates=[_candidate()],
        output_dir=tmp_path,
        config={"enable_predictive_models": True, "strict_predictive_models": True},
    )

    with pytest.raises(AgentExecutionError, match="Predictive model prediction failed"):
        PredictiveModelAgent(
            registry=_Registry([_model_card()]),
            predictor=_FailingPredictor(),
        ).run(context)


class _Registry:
    def __init__(self, cards: list[ModelCard]) -> None:
        self.cards = cards

    def list_models(self, **_kwargs: Any) -> list[ModelCard]:
        return self.cards


class _Predictor:
    def __init__(self, *, applicability_domain: str = "in_domain") -> None:
        self.applicability_domain = applicability_domain

    def predict(
        self,
        model_card: ModelCard,
        candidates: list[Any],
        features: Any,
        config: dict[str, Any],
    ) -> list[ModelPrediction]:
        del features, config
        return [
            ModelPrediction(
                prediction_id=f"prediction-{index}",
                model_id=model_card.model_id,
                model_version=model_card.model_version,
                endpoint_id=model_card.endpoint.endpoint_id,
                candidate_id=str(candidate.get("candidate_id") or candidate.get("candidate_name")),
                candidate_name=str(candidate["candidate_name"]),
                candidate_origin=candidate["candidate_origin"],
                canonical_smiles=candidate.get("canonical_smiles"),
                inchi_key=candidate.get("inchi_key"),
                predicted_value=True,
                predicted_probability=0.7,
                prediction_label="surrogate_positive",
                uncertainty=0.25,
                confidence=0.75,
                applicability_domain=self.applicability_domain,  # type: ignore[arg-type]
                calibration_status="uncalibrated",
                explanation="Mock prediction.",
                warnings=["not evidence"],
                created_at=datetime(2026, 1, 4, tzinfo=UTC),
                metadata={"not_experimental_evidence": True, "not_assay_result": True},
            )
            for index, candidate in enumerate(candidates)
        ]


class _FailingPredictor:
    def predict(
        self,
        model_card: ModelCard,
        candidates: list[Any],
        features: Any,
        config: dict[str, Any],
    ) -> list[ModelPrediction]:
        del model_card, candidates, features, config
        raise RuntimeError("boom")
