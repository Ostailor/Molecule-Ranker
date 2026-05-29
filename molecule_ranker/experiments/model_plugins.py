"""Formal predictive model plugin contracts for assay-specific surrogates."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from molecule_ranker.experiments.schemas import ExperimentalLearningDataset
from molecule_ranker.experiments.surrogate_model import (
    SurrogateModelArtifact,
    predict_assay_surrogate_outcomes,
    train_assay_surrogate_model,
)

ProviderKind = Literal["local", "external"]


@dataclass(frozen=True)
class ModelPluginSpec:
    plugin_name: str
    plugin_version: str
    interface_version: str = "1.2"
    provider_kind: ProviderKind = "local"
    supports_training: bool = True
    supports_prediction: bool = True
    allowed_output_kind: str = "prediction_artifact"
    requires_network: bool = False
    safety_boundaries: tuple[str, ...] = (
        "predictions_are_not_biomedical_evidence",
        "predictions_are_not_assay_results",
        "predictions_must_not_become_evidence_items",
        "endpoint_specific_context_required",
        "patient_clinical_and_dosing_data_forbidden",
    )


@dataclass(frozen=True)
class ModelTrainingRequest:
    dataset: ExperimentalLearningDataset
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelPredictionRequest:
    model_artifact: SurrogateModelArtifact
    rows: list[dict[str, Any]]
    config: dict[str, Any] = field(default_factory=dict)


class ModelPlugin(ABC):
    spec: ModelPluginSpec

    @abstractmethod
    def train(self, request: ModelTrainingRequest) -> SurrogateModelArtifact:
        """Train or load a model artifact from an endpoint-specific dataset."""

    @abstractmethod
    def predict(self, request: ModelPredictionRequest) -> list[dict[str, Any]]:
        """Return guarded prediction artifacts, never evidence items or assay results."""


class LocalAssaySurrogatePlugin(ModelPlugin):
    spec = ModelPluginSpec(
        plugin_name="local_assay_surrogate",
        plugin_version="1.2.0",
        provider_kind="local",
        requires_network=False,
    )

    def train(self, request: ModelTrainingRequest) -> SurrogateModelArtifact:
        _validate_training_request(request)
        return train_assay_surrogate_model(request.dataset, config=request.config)

    def predict(self, request: ModelPredictionRequest) -> list[dict[str, Any]]:
        if request.model_artifact.metadata.get("evidence_boundary") not in {
            None,
            "not_experimental_evidence",
        }:
            raise ValueError("Model artifacts must remain outside experimental evidence.")
        predictions = predict_assay_surrogate_outcomes(request.model_artifact, request.rows)
        for prediction in predictions:
            if prediction.get("artifact_kind") != self.spec.allowed_output_kind:
                raise ValueError("Plugin prediction output must be a prediction artifact.")
            if prediction.get("evidence_boundary") != "not_experimental_evidence":
                raise ValueError("Plugin predictions must not cross the evidence boundary.")
        return predictions


class ModelPluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, ModelPlugin] = {}

    def register(self, plugin: ModelPlugin) -> None:
        spec = plugin.spec
        if spec.allowed_output_kind != "prediction_artifact":
            raise ValueError("Model plugins may only emit prediction artifacts.")
        if spec.requires_network and spec.provider_kind != "external":
            raise ValueError("Only external model providers may require network access.")
        self._plugins[spec.plugin_name] = plugin

    def get(self, plugin_name: str) -> ModelPlugin:
        try:
            return self._plugins[plugin_name]
        except KeyError:
            raise ValueError(f"Unknown model plugin: {plugin_name}") from None

    def list_specs(self) -> list[ModelPluginSpec]:
        return [plugin.spec for plugin in self._plugins.values()]


def _validate_training_request(request: ModelTrainingRequest) -> None:
    dataset = request.dataset
    if request.config.get("use_patient_data") or request.config.get("use_clinical_data"):
        raise ValueError("Model training must not use patient or clinical data.")
    if not dataset.endpoint_name:
        raise ValueError("Model training requires an assay endpoint.")
    if request.config.get("allow_endpoint_pooling") and not request.config.get(
        "pooled_endpoint_label"
    ):
        raise ValueError("Endpoint pooling must be explicitly configured and labeled.")


__all__ = [
    "LocalAssaySurrogatePlugin",
    "ModelPlugin",
    "ModelPluginRegistry",
    "ModelPluginSpec",
    "ModelPredictionRequest",
    "ModelTrainingRequest",
]
