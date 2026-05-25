from __future__ import annotations

import pytest

from molecule_ranker.developability.admet_models import (
    ExternalADMETModel,
    LocalSklearnADMETModel,
    ModelCard,
    ModelUnavailableError,
    RuleBasedADMETModel,
)


def _model_card(**overrides):
    payload = {
        "model_name": "local-test-admet",
        "model_version": "0.1",
        "training_data": "not packaged; test metadata only",
        "endpoints": ["solubility_risk", "ames_mutagenicity_risk"],
        "metrics": {"auroc": 0.7},
        "applicability_domain_method": "descriptor-range placeholder",
        "license": "not distributed",
        "intended_use": "computational triage research only",
        "limitations": ["No model weights are shipped with molecule-ranker."],
        "source": "unit-test",
    }
    payload.update(overrides)
    return ModelCard(**payload)


def test_rule_based_model_returns_predictions():
    model = RuleBasedADMETModel(origin="generated")

    result = model.predict(["CCO"])

    assert "CCO" in result
    assert result["CCO"]
    assert all(prediction.prediction_method == "rule_based" for prediction in result["CCO"])


def test_missing_local_model_raises_model_unavailable(tmp_path):
    with pytest.raises(ModelUnavailableError, match="unavailable"):
        LocalSklearnADMETModel(
            model_path=tmp_path / "missing.joblib",
            model_card=_model_card(),
        )


def test_fallback_to_rule_based_only_when_allowed(tmp_path):
    model = LocalSklearnADMETModel(
        model_path=tmp_path / "missing.joblib",
        model_card=_model_card(),
        allow_rule_based_admet_fallback=True,
    )

    result = model.predict(["CCO"])

    assert result["CCO"]
    assert all(prediction.prediction_method == "rule_based" for prediction in result["CCO"])


def test_model_card_is_required_for_local_ml_model(tmp_path):
    with pytest.raises(ValueError, match="Model card metadata is required"):
        LocalSklearnADMETModel(
            model_path=tmp_path / "missing.joblib",
            model_card=None,
        )


def test_model_card_is_required_for_external_model():
    with pytest.raises(ValueError, match="Model card metadata is required"):
        ExternalADMETModel(model_card=None)


def test_external_model_placeholder_does_not_call_services():
    model = ExternalADMETModel(model_card=_model_card(model_name="external-test"))

    with pytest.raises(ModelUnavailableError, match="does not call web services"):
        model.predict(["CCO"])
