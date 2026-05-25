from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from molecule_ranker.developability.admet import (
    ENDPOINTS,
    MODEL_NAME,
    MODEL_VERSION,
    predict_rule_based_admet,
)
from molecule_ranker.developability.descriptors import compute_physchem_profile
from molecule_ranker.developability.filters import detect_chemistry_alerts
from molecule_ranker.developability.schemas import ADMETPrediction


class ModelUnavailableError(RuntimeError):
    """Raised when a requested ADMET model is unavailable or intentionally unloaded."""


class ModelCard(BaseModel):
    model_name: str
    model_version: str | None = None
    training_data: str
    endpoints: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    applicability_domain_method: str
    license: str
    intended_use: str
    limitations: list[str] = Field(default_factory=list)
    source: str


class ADMETModel(Protocol):
    model_name: str
    model_version: str | None
    endpoints: list[str]

    def predict(self, smiles: list[str]) -> dict[str, list[ADMETPrediction]]: ...


class RuleBasedADMETModel:
    model_name = MODEL_NAME
    model_version = MODEL_VERSION
    endpoints = list(ENDPOINTS)

    def __init__(
        self,
        *,
        origin: str = "generated",
        warning_evidence_by_smiles: Mapping[str, list[Any]] | None = None,
    ) -> None:
        self.origin = origin
        self.warning_evidence_by_smiles = dict(warning_evidence_by_smiles or {})

    def predict(self, smiles: list[str]) -> dict[str, list[ADMETPrediction]]:
        predictions: dict[str, list[ADMETPrediction]] = {}
        for value in smiles:
            profile = compute_physchem_profile(value)
            alerts = detect_chemistry_alerts(value)
            predictions[value] = predict_rule_based_admet(
                profile,
                alerts,
                self.origin,
                warning_evidence=self.warning_evidence_by_smiles.get(value, []),
            )
        return predictions


class LocalSklearnADMETModel:
    def __init__(
        self,
        *,
        model_path: str | Path,
        model_card: ModelCard | None,
        allow_rule_based_admet_fallback: bool = False,
        fallback_model: ADMETModel | None = None,
    ) -> None:
        if model_card is None:
            raise ValueError("Model card metadata is required for local ML ADMET models.")
        self.model_path = Path(model_path)
        self.model_card = model_card
        self.model_name = model_card.model_name
        self.model_version = model_card.model_version
        self.endpoints = list(model_card.endpoints)
        self._fallback_model = fallback_model or RuleBasedADMETModel()
        self._fallback_enabled = allow_rule_based_admet_fallback
        self._model_loaded = False
        if not self.model_path.exists():
            if self._fallback_enabled:
                return
            raise ModelUnavailableError(
                f"Local ADMET model file is unavailable: {self.model_path}"
            )
        raise ModelUnavailableError(
            "Local sklearn ADMET model loading is not implemented in V0.4 baseline; "
            "provide an integration adapter before using model files."
        )

    def predict(self, smiles: list[str]) -> dict[str, list[ADMETPrediction]]:
        if self._fallback_enabled and not self._model_loaded:
            return self._fallback_model.predict(smiles)
        raise ModelUnavailableError(
            "Local sklearn ADMET model is unavailable; no predictions were produced."
        )


class ExternalADMETModel:
    def __init__(
        self,
        *,
        model_card: ModelCard | None,
        model_name: str | None = None,
        model_version: str | None = None,
        endpoints: list[str] | None = None,
    ) -> None:
        if model_card is None:
            raise ValueError("Model card metadata is required for external ADMET models.")
        self.model_card = model_card
        self.model_name = model_name or model_card.model_name
        self.model_version = model_version or model_card.model_version
        self.endpoints = list(endpoints or model_card.endpoints)

    def predict(self, smiles: list[str]) -> dict[str, list[ADMETPrediction]]:
        raise ModelUnavailableError(
            "External ADMET model interface is a placeholder and does not call web services."
        )


__all__ = [
    "ADMETModel",
    "ExternalADMETModel",
    "LocalSklearnADMETModel",
    "ModelCard",
    "ModelUnavailableError",
    "RuleBasedADMETModel",
]
