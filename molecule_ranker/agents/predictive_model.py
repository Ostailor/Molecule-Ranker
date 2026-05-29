from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from molecule_ranker.agents.base import AgentExecutionError, BaseAgent, PipelineContext
from molecule_ranker.models.features import featurize_model_rows
from molecule_ranker.models.plugin import RuleBasedSurrogatePlugin
from molecule_ranker.models.registry import ModelRegistry
from molecule_ranker.models.schemas import ModelCard, ModelPrediction
from molecule_ranker.schemas import GeneratedMoleculeHypothesis, MoleculeCandidate


class _ModelRegistry(Protocol):
    def list_models(
        self,
        *,
        active_only: bool = True,
        endpoint_id: str | None = None,
        plugin_name: str | None = None,
    ) -> list[ModelCard]: ...


class _Predictor(Protocol):
    def predict(
        self,
        model_card: ModelCard,
        candidates: list[Any],
        features: Any,
        config: dict[str, Any],
    ) -> list[ModelPrediction]: ...


class PredictiveModelAgent(BaseAgent):
    """Run calibrated surrogate predictions without creating evidence."""

    name = "PredictiveModelAgent"

    def __init__(
        self,
        *,
        registry: _ModelRegistry | None = None,
        predictor: _Predictor | None = None,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._predictor = predictor

    def process(self, context: PipelineContext) -> PipelineContext:
        if not bool(context.config.get("enable_predictive_models", False)):
            context.config[self._trace_key] = {
                "enabled": False,
                "model_ids": [],
                "endpoint_ids": [],
                "prediction_count": 0,
                "out_of_domain_count": 0,
                "warnings": [],
            }
            return context

        strict = bool(context.config.get("strict_predictive_models", False))
        warnings: list[str] = []
        predictions: list[ModelPrediction] = []
        cards = self._load_cards(context, strict=strict, warnings=warnings)
        rows = _candidate_rows(context)
        for card in cards:
            try:
                feature_result = featurize_model_rows(
                    rows,
                    feature_spec=card.feature_spec,
                    config=dict(context.config.get("predictive_model_feature_config") or {}),
                )
                card_predictions = self._active_predictor(context).predict(
                    card,
                    rows,
                    feature_result.rows,
                    dict(context.config.get("predictive_model_prediction_config") or {}),
                )
                _validate_predictions(card_predictions)
                predictions.extend(card_predictions)
            except Exception as exc:
                message = f"Predictive model prediction failed for {card.model_id}: {exc}"
                if strict:
                    raise AgentExecutionError(message) from exc
                warnings.append(message)

        prediction_payload = [prediction.model_dump(mode="json") for prediction in predictions]
        context.config["model_predictions"] = prediction_payload
        warnings.extend(_prediction_warnings(predictions))
        if warnings:
            context.config.setdefault("warnings", []).extend(warnings)
        context.config[f"{self.name}.warnings"] = sorted(set(warnings))
        context.candidates = _attach_candidate_prediction_summaries(
            context.candidates,
            predictions,
        )
        context.generated_candidates = _attach_generated_prediction_summaries(
            context.generated_candidates,
            predictions,
        )
        artifact_path = _write_predictions_artifact(context, prediction_payload)
        context.config["model_predictions_json"] = str(artifact_path)
        context.config[self._trace_key] = {
            "enabled": True,
            "model_ids": sorted({prediction.model_id for prediction in predictions}),
            "endpoint_ids": sorted({prediction.endpoint_id for prediction in predictions}),
            "prediction_count": len(predictions),
            "out_of_domain_count": sum(
                1
                for prediction in predictions
                if prediction.applicability_domain == "out_of_domain"
            ),
            "warnings": sorted(set(warnings)),
            "artifact_path": str(artifact_path),
            "claim_boundary": "predictions_not_evidence_not_assay_results",
        }
        return context

    def summarize_output(self, context: PipelineContext) -> str:
        metadata = context.config.get(self._trace_key, {})
        if not metadata.get("enabled", False):
            return "Predictive models disabled; skipped surrogate predictions."
        return (
            f"Generated {metadata.get('prediction_count', 0)} predictive model "
            f"artifact prediction(s)."
        )

    def trace_metadata(self, context: PipelineContext) -> dict[str, Any]:
        return dict(
            context.config.get(
                self._trace_key,
                {
                    "enabled": False,
                    "prediction_count": 0,
                    "out_of_domain_count": 0,
                    "warnings": [],
                },
            )
        )

    @property
    def _trace_key(self) -> str:
        return "PredictiveModelAgent.trace_metadata"

    def _load_cards(
        self,
        context: PipelineContext,
        *,
        strict: bool,
        warnings: list[str],
    ) -> list[ModelCard]:
        try:
            registry = self._active_registry(context)
            endpoint_id = context.config.get("predictive_model_endpoint_id")
            plugin_name = context.config.get("predictive_model_plugin_name")
            return registry.list_models(
                active_only=True,
                endpoint_id=str(endpoint_id) if endpoint_id else None,
                plugin_name=str(plugin_name) if plugin_name else None,
            )
        except Exception as exc:
            message = f"Predictive model loading failed: {exc}"
            if strict:
                raise AgentExecutionError(message) from exc
            warnings.append(message)
            return []

    def _active_registry(self, context: PipelineContext) -> _ModelRegistry:
        if self._registry is not None:
            return self._registry
        db_path = Path(str(context.config.get("model_registry_db_path") or "models.sqlite"))
        artifact_dir = Path(
            str(context.config.get("model_registry_artifact_dir") or "model_artifacts")
        )
        return ModelRegistry(db_path=db_path, artifact_dir=artifact_dir)

    def _active_predictor(self, context: PipelineContext) -> _Predictor:
        del context
        return self._predictor or RuleBasedSurrogatePlugin()


def _candidate_rows(context: PipelineContext) -> list[dict[str, Any]]:
    rows = [_row_from_candidate(candidate) for candidate in context.candidates]
    rows.extend(_row_from_generated(candidate) for candidate in context.generated_candidates)
    return rows


def _row_from_candidate(candidate: MoleculeCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.identifiers.get("chembl") or candidate.name,
        "candidate_name": candidate.name,
        "candidate_origin": candidate.origin,
        "canonical_smiles": candidate.chemical_metadata.get("canonical_smiles"),
        "inchi_key": candidate.chemical_metadata.get("inchi_key"),
        "target_symbol": candidate.known_targets[0] if candidate.known_targets else None,
        "metadata": {
            **candidate.chemical_metadata,
            **candidate.generation_metadata,
        },
    }


def _row_from_generated(candidate: GeneratedMoleculeHypothesis) -> dict[str, Any]:
    return {
        "candidate_id": candidate.name,
        "candidate_name": candidate.name,
        "candidate_origin": "generated",
        "canonical_smiles": candidate.canonical_smiles,
        "inchi_key": candidate.trace.get("inchi_key"),
        "target_symbol": candidate.target_symbol,
        "generation_method": candidate.source,
        "metadata": {
            **candidate.trace,
            "generator_method": candidate.source,
            "distance_to_seed": 1.0 - candidate.max_seed_similarity,
        },
    }


def _validate_predictions(predictions: list[ModelPrediction]) -> None:
    for prediction in predictions:
        if prediction.applicability_domain not in {
            "in_domain",
            "near_domain",
            "out_of_domain",
            "unknown",
        }:
            raise ValueError("Prediction missing valid applicability domain.")
        if prediction.uncertainty < 0.0 or prediction.uncertainty > 1.0:
            raise ValueError("Prediction uncertainty must be in [0, 1].")


def _prediction_warnings(predictions: list[ModelPrediction]) -> list[str]:
    warnings = []
    for prediction in predictions:
        if prediction.applicability_domain == "out_of_domain":
            warnings.append("out_of_domain_prediction")
        warnings.extend(prediction.warnings)
    return sorted(set(warnings))


def _attach_candidate_prediction_summaries(
    candidates: list[MoleculeCandidate],
    predictions: list[ModelPrediction],
) -> list[MoleculeCandidate]:
    by_name = _predictions_by_candidate_name(predictions)
    updated: list[MoleculeCandidate] = []
    for candidate in candidates:
        summaries = [
            _prediction_summary(prediction) for prediction in by_name.get(candidate.name, [])
        ]
        if not summaries:
            updated.append(candidate)
            continue
        if candidate.origin == "generated":
            updated.append(
                candidate.model_copy(
                    update={
                        "generation_metadata": {
                            **candidate.generation_metadata,
                            "model_predictions": summaries,
                            "model_prediction_score_modifier": _bounded_modifier(summaries),
                        }
                    }
                )
            )
        else:
            updated.append(
                candidate.model_copy(
                    update={
                        "chemical_metadata": {
                            **candidate.chemical_metadata,
                            "model_predictions": summaries,
                            "model_prediction_score_modifier": _bounded_modifier(summaries),
                        }
                    }
                )
            )
    return updated


def _attach_generated_prediction_summaries(
    candidates: list[GeneratedMoleculeHypothesis],
    predictions: list[ModelPrediction],
) -> list[GeneratedMoleculeHypothesis]:
    by_name = _predictions_by_candidate_name(predictions)
    updated = []
    for candidate in candidates:
        summaries = [
            _prediction_summary(prediction) for prediction in by_name.get(candidate.name, [])
        ]
        if not summaries:
            updated.append(candidate)
            continue
        updated.append(
            candidate.model_copy(
                update={
                    "trace": {
                        **candidate.trace,
                        "model_predictions": summaries,
                        "model_prediction_score_modifier": _bounded_modifier(summaries),
                    }
                }
            )
        )
    return updated


def _predictions_by_candidate_name(
    predictions: list[ModelPrediction],
) -> dict[str, list[ModelPrediction]]:
    grouped: dict[str, list[ModelPrediction]] = {}
    for prediction in predictions:
        grouped.setdefault(prediction.candidate_name, []).append(prediction)
    return grouped


def _prediction_summary(prediction: ModelPrediction) -> dict[str, Any]:
    return {
        "prediction_id": prediction.prediction_id,
        "model_id": prediction.model_id,
        "model_version": prediction.model_version,
        "endpoint_id": prediction.endpoint_id,
        "predicted_value": prediction.predicted_value,
        "predicted_probability": prediction.predicted_probability,
        "prediction_label": prediction.prediction_label,
        "uncertainty": prediction.uncertainty,
        "confidence": prediction.confidence,
        "applicability_domain": prediction.applicability_domain,
        "calibration_status": prediction.calibration_status,
        "warnings": prediction.warnings,
        "not_evidence": True,
        "not_assay_result": True,
    }


def _bounded_modifier(summaries: list[dict[str, Any]]) -> float:
    confidences = [
        float(summary["confidence"])
        for summary in summaries
        if isinstance(summary.get("confidence"), int | float)
    ]
    if not confidences:
        return 0.0
    return max(-0.05, min(0.05, (sum(confidences) / len(confidences) - 0.5) * 0.1))


def _write_predictions_artifact(
    context: PipelineContext,
    predictions: list[dict[str, Any]],
) -> Path:
    output_dir = context.output_dir or Path(str(context.config.get("results_dir") or "results"))
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "model_predictions.json"
    path.write_text(
        json.dumps(
            {
                "artifact_type": "ModelPredictionArtifact",
                "predictions": predictions,
                "limitations": [
                    "Predictions are not biomedical evidence.",
                    "Predictions are not assay results.",
                    "Generated molecules require exact imported experimental results for evidence.",
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return path


__all__ = ["PredictiveModelAgent"]
