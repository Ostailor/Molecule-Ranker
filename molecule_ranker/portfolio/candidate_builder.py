from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from molecule_ranker.experiments.schemas import AssayResult
from molecule_ranker.generation.schemas import GeneratedMolecule
from molecule_ranker.schemas import GeneratedMoleculeHypothesis, MoleculeCandidate

from .risk import blocking_risks_from_text, risk_flags_from_text
from .schemas import PortfolioCandidate
from .uncertainty import uncertainty_from_confidence

PORTFOLIO_SCORE_FIELDS = (
    "evidence_score",
    "generation_score",
    "developability_score",
    "experimental_support_score",
    "predictive_model_score",
    "structure_score",
    "experiment_readiness_score",
    "uncertainty_score",
    "novelty_score",
)


def build_portfolio_candidates(
    *,
    existing_candidates: Sequence[MoleculeCandidate] = (),
    generated_molecules: Sequence[GeneratedMolecule | GeneratedMoleculeHypothesis] = (),
    experimental_results: Sequence[AssayResult] = (),
    disease_name: str | None = None,
) -> list[PortfolioCandidate]:
    results_by_key = _index_results(experimental_results)
    candidates = [
        _candidate_from_existing(candidate, results_by_key, disease_name)
        for candidate in existing_candidates
    ]
    for molecule in generated_molecules:
        if isinstance(molecule, GeneratedMoleculeHypothesis):
            candidates.append(
                _candidate_from_generated_hypothesis(molecule, results_by_key, disease_name)
            )
        else:
            candidates.append(_candidate_from_generated(molecule, results_by_key, disease_name))
    return candidates


def build_portfolio_candidates_from_artifacts(run_dir: Path | str) -> list[PortfolioCandidate]:
    """Build portfolio candidates from a run artifact directory.

    This function keeps model, structure, review, and active-learning signals
    separate from evidence fields so downstream portfolio logic cannot promote
    computational artifacts into biomedical evidence.
    """

    root = Path(run_dir)
    artifact_payloads = _load_artifact_payloads(root)
    disease_name = _disease_name_from_artifacts(artifact_payloads)
    candidates = _base_candidates_from_artifacts(artifact_payloads, disease_name=disease_name)
    index = _CandidateIndex(candidates)

    _apply_developability_artifact(candidates, index, artifact_payloads.get("developability.json"))
    _apply_experimental_evidence_artifact(
        candidates,
        index,
        artifact_payloads.get("experimental_evidence.json"),
    )
    _apply_model_predictions_artifact(
        candidates,
        index,
        artifact_payloads.get("model_predictions.json"),
    )
    _apply_structure_artifact(
        candidates,
        index,
        artifact_payloads.get("structure_aware_assessments.json"),
    )
    _apply_review_artifact(candidates, index, artifact_payloads.get("review_queue.json"))
    _apply_active_learning_artifact(
        candidates,
        index,
        artifact_payloads.get("active_learning_batch.json"),
    )
    for name in (
        "experiment_readiness.json",
        "design/experiment_readiness.json",
        "design/readiness.json",
    ):
        _apply_readiness_artifact(
            candidates, index, artifact_payloads.get(name), artifact_name=name
        )
    for name in ("integration_mappings.json", "external_mappings.json", "mappings.json"):
        _apply_external_mappings(candidates, index, artifact_payloads.get(name), artifact_name=name)

    for candidate in candidates:
        _finalize_artifact_candidate(candidate)
    return candidates


def build_candidates_from_artifacts(run_dir: Path | str) -> list[PortfolioCandidate]:
    return build_portfolio_candidates_from_artifacts(run_dir)


def _candidate_from_existing(
    candidate: MoleculeCandidate,
    results_by_key: Mapping[str, list[AssayResult]],
    disease_name: str | None,
) -> PortfolioCandidate:
    candidate_id = _existing_candidate_id(candidate)
    exact_results = _matching_results(candidate_id, candidate.name, None, results_by_key)
    evidence_score = _bounded_number(candidate.score) or 0.0
    if candidate.score_breakdown is not None:
        evidence_score = max(
            evidence_score,
            _bounded_number(candidate.score_breakdown.final_score) or 0.0,
        )
    developability_score = _developability_score(candidate.developability_assessment)
    confidence = candidate.score_breakdown.confidence if candidate.score_breakdown else None
    warnings = list(candidate.warnings)
    return PortfolioCandidate(
        portfolio_candidate_id=candidate_id,
        source_candidate_id=candidate_id,
        candidate_name=candidate.name,
        origin="existing",
        canonical_smiles=_string_or_none(candidate.chemical_metadata.get("canonical_smiles")),
        inchi_key=_string_or_none(candidate.chemical_metadata.get("inchi_key")),
        disease_name=disease_name,
        target_symbols=sorted(set(candidate.known_targets)),
        mechanism_label=candidate.mechanism_of_action,
        chemical_series_id=_metadata_string(candidate.chemical_metadata, "chemical_series"),
        scaffold_id=_metadata_string(candidate.chemical_metadata, "scaffold_id"),
        evidence_score=evidence_score,
        generation_score=None,
        developability_score=developability_score,
        experimental_support_score=_experimental_feedback_score(exact_results),
        predictive_model_score=_metadata_score(
            candidate.generation_metadata, "predictive_model_score"
        ),
        structure_score=_metadata_score(candidate.chemical_metadata, "structure_score"),
        experiment_readiness_score=_bounded_number(candidate.score),
        uncertainty_score=uncertainty_from_confidence(confidence),
        novelty_score=_bounded_number(
            candidate.score_breakdown.novelty_or_repurposing_value
            if candidate.score_breakdown
            else None
        ),
        diversity_features={
            "chemical_series_id": _metadata_string(candidate.chemical_metadata, "chemical_series")
        },
        risk_flags=risk_flags_from_text(warnings),
        blocking_risks=blocking_risks_from_text(warnings),
        review_status=None,
        direct_experimental_evidence=bool(exact_results),
        generated_without_direct_evidence=False,
        metadata={
            "deterministic_source": "MoleculeCandidate",
            "exact_experimental_evidence_result_ids": [
                result.result_id for result in exact_results
            ],
        },
    )


def _candidate_from_generated(
    candidate: GeneratedMolecule,
    results_by_key: Mapping[str, list[AssayResult]],
    disease_name: str | None,
) -> PortfolioCandidate:
    exact_results = _matching_results(
        candidate.generated_id,
        candidate.generated_id,
        candidate.canonical_smiles,
        results_by_key,
    )
    breakdown = candidate.score_breakdown
    generation_score = _bounded_number(candidate.generation_score) or 0.0
    if breakdown is not None:
        generation_score = max(
            generation_score,
            _bounded_number(breakdown.final_generation_score) or 0.0,
        )
    warnings = [*candidate.warnings, *candidate.validation.rejection_reasons]
    direct_evidence = bool(exact_results)
    return PortfolioCandidate(
        portfolio_candidate_id=candidate.generated_id,
        source_candidate_id=candidate.generated_id,
        candidate_name=candidate.generated_id,
        origin="generated",
        canonical_smiles=candidate.canonical_smiles,
        inchi_key=candidate.inchi_key,
        disease_name=disease_name,
        target_symbols=sorted(set(candidate.conditioned_targets)),
        mechanism_label=None,
        chemical_series_id=candidate.diversity_cluster,
        scaffold_id=_string_or_none(
            candidate.metadata.get("scaffold_id") or candidate.diversity_cluster
        ),
        evidence_score=_experimental_feedback_score(exact_results),
        generation_score=generation_score,
        developability_score=_developability_score(candidate.developability_assessment),
        experimental_support_score=_experimental_feedback_score(exact_results),
        predictive_model_score=_model_prediction_score(candidate.metadata),
        structure_score=_metadata_score(candidate.metadata, "structure_score"),
        experiment_readiness_score=_breakdown_score(breakdown, "experiment_readiness_score"),
        uncertainty_score=_breakdown_score(breakdown, "uncertainty_score")
        or _metadata_score(candidate.metadata, "uncertainty", "active_learning_value"),
        novelty_score=_breakdown_score(breakdown, "novelty_score"),
        diversity_features={
            "diversity_cluster": candidate.diversity_cluster,
            "descriptors": dict(candidate.descriptors),
        },
        risk_flags=risk_flags_from_text(warnings),
        blocking_risks=blocking_risks_from_text(warnings),
        review_status=None,
        direct_experimental_evidence=direct_evidence,
        generated_without_direct_evidence=not direct_evidence,
        metadata={
            "deterministic_source": "GeneratedMolecule",
            "source_warnings": list(candidate.warnings),
            "exact_experimental_evidence_result_ids": [
                result.result_id for result in exact_results
            ],
            "generated_hypothesis_only": not direct_evidence,
        },
    )


def _candidate_from_generated_hypothesis(
    candidate: GeneratedMoleculeHypothesis,
    results_by_key: Mapping[str, list[AssayResult]],
    disease_name: str | None,
) -> PortfolioCandidate:
    exact_results = _matching_results(
        candidate.name,
        candidate.name,
        candidate.canonical_smiles,
        results_by_key,
    )
    direct_evidence = bool(exact_results)
    return PortfolioCandidate(
        portfolio_candidate_id=candidate.name,
        source_candidate_id=candidate.name,
        candidate_name=candidate.name,
        origin="generated",
        canonical_smiles=candidate.canonical_smiles,
        inchi_key=_string_or_none(candidate.trace.get("inchi_key")),
        disease_name=disease_name,
        target_symbols=[candidate.target_symbol] if candidate.target_symbol else [],
        mechanism_label=None,
        chemical_series_id=_string_or_none(candidate.trace.get("diversity_cluster")),
        scaffold_id=_string_or_none(candidate.trace.get("scaffold_id")),
        evidence_score=_experimental_feedback_score(exact_results),
        generation_score=_bounded_number(candidate.generation_score),
        developability_score=_developability_score(candidate.developability_assessment),
        experimental_support_score=_experimental_feedback_score(exact_results),
        predictive_model_score=_metadata_score(candidate.trace, "predictive_model_score"),
        structure_score=_metadata_score(candidate.trace, "structure_score"),
        experiment_readiness_score=_metadata_score(candidate.trace, "experiment_readiness_score"),
        uncertainty_score=_metadata_score(candidate.trace, "uncertainty_score") or 0.55,
        novelty_score=_bounded_number(candidate.descriptors.get("novelty_score")),
        diversity_features=dict(candidate.descriptors),
        risk_flags=risk_flags_from_text(candidate.warnings),
        blocking_risks=blocking_risks_from_text(candidate.warnings),
        review_status=None,
        direct_experimental_evidence=direct_evidence,
        generated_without_direct_evidence=not direct_evidence,
        metadata={
            "deterministic_source": "GeneratedMoleculeHypothesis",
            "source_warnings": list(candidate.warnings),
            "exact_experimental_evidence_result_ids": [
                result.result_id for result in exact_results
            ],
            "generated_hypothesis_only": not direct_evidence,
        },
    )


def _load_artifact_payloads(root: Path) -> dict[str, Any]:
    names = [
        "candidates.json",
        "generated_candidates.json",
        "developability.json",
        "experimental_evidence.json",
        "model_predictions.json",
        "structure_aware_assessments.json",
        "review_queue.json",
        "active_learning_batch.json",
        "experiment_readiness.json",
        "design/experiment_readiness.json",
        "design/readiness.json",
        "integration_mappings.json",
        "external_mappings.json",
        "mappings.json",
    ]
    return {name: _read_optional_json(root / name) for name in names if (root / name).exists()}


def _read_optional_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON artifact: {path}") from exc


def _disease_name_from_artifacts(artifact_payloads: Mapping[str, Any]) -> str | None:
    candidates_payload = _as_mapping(artifact_payloads.get("candidates.json"))
    disease = _as_mapping(candidates_payload.get("disease"))
    return _string_or_none(
        disease.get("canonical_name")
        or disease.get("input_name")
        or candidates_payload.get("disease_name")
    )


def _base_candidates_from_artifacts(
    artifact_payloads: Mapping[str, Any],
    *,
    disease_name: str | None,
) -> list[PortfolioCandidate]:
    candidates: list[PortfolioCandidate] = []
    candidates_payload = _as_mapping(artifact_payloads.get("candidates.json"))
    for raw in _records_from_payload(candidates_payload, ("candidates", "items", "records")):
        candidates.append(_candidate_from_raw_candidate(raw, disease_name=disease_name))

    generated_payload = artifact_payloads.get("generated_candidates.json")
    for raw in _generated_records_from_payload(generated_payload):
        candidates.append(_candidate_from_raw_generated(raw, disease_name=disease_name))
    return candidates


def _candidate_from_raw_candidate(
    raw: Mapping[str, Any],
    *,
    disease_name: str | None,
) -> PortfolioCandidate:
    identifiers = _as_mapping(raw.get("identifiers"))
    chemical_metadata = _as_mapping(raw.get("chemical_metadata"))
    score_breakdown = _as_mapping(raw.get("score_breakdown"))
    origin = str(raw.get("origin") or "existing")
    if origin not in {"existing", "generated", "external"}:
        origin = "existing"
    source_id = _first_string(
        raw.get("candidate_id"),
        raw.get("source_candidate_id"),
        identifiers.get("chembl"),
        identifiers.get("chembl_id"),
        identifiers.get("pubchem_cid"),
        identifiers.get("cid"),
        identifiers.get("inchikey"),
        raw.get("name"),
        raw.get("candidate_name"),
    )
    candidate_name = (
        _first_string(raw.get("name"), raw.get("candidate_name"), source_id) or "candidate"
    )
    warnings = [str(item) for item in _as_list(raw.get("warnings"))]
    direct = bool(raw.get("direct_experimental_evidence") or raw.get("direct_evidence_available"))
    return PortfolioCandidate(
        portfolio_candidate_id=source_id or _stable_candidate_id(candidate_name),
        source_candidate_id=source_id,
        candidate_name=candidate_name,
        origin=origin,  # type: ignore[arg-type]
        canonical_smiles=_first_string(
            raw.get("canonical_smiles"), chemical_metadata.get("canonical_smiles")
        ),
        inchi_key=_first_string(
            raw.get("inchi_key"), raw.get("inchiKey"), chemical_metadata.get("inchi_key")
        ),
        disease_name=_first_string(raw.get("disease_name"), disease_name),
        target_symbols=_string_list(raw.get("target_symbols") or raw.get("known_targets")),
        mechanism_label=_first_string(raw.get("mechanism_label"), raw.get("mechanism_of_action")),
        chemical_series_id=_first_string(
            raw.get("chemical_series_id"),
            chemical_metadata.get("chemical_series"),
        ),
        scaffold_id=_first_string(raw.get("scaffold_id"), chemical_metadata.get("scaffold_id")),
        evidence_score=_score_or_none(
            raw.get("evidence_score"), raw.get("score"), score_breakdown.get("final_score")
        ),
        generation_score=_score_or_none(raw.get("generation_score")),
        developability_score=_score_or_none(
            raw.get("developability_score"),
            score_breakdown.get("developability_score"),
        ),
        experimental_support_score=None,
        predictive_model_score=None,
        structure_score=_score_or_none(
            raw.get("structure_score"), chemical_metadata.get("structure_score")
        ),
        experiment_readiness_score=_score_or_none(raw.get("experiment_readiness_score")),
        uncertainty_score=_score_or_none(raw.get("uncertainty_score")),
        novelty_score=_score_or_none(
            raw.get("novelty_score"),
            score_breakdown.get("novelty_or_repurposing_value"),
        ),
        diversity_features={
            "chemical_series_id": _first_string(
                raw.get("chemical_series_id"),
                chemical_metadata.get("chemical_series"),
            ),
            "scaffold_id": _first_string(
                raw.get("scaffold_id"), chemical_metadata.get("scaffold_id")
            ),
        },
        risk_flags=risk_flags_from_text(warnings),
        blocking_risks=blocking_risks_from_text(warnings),
        review_status=None,
        direct_experimental_evidence=direct,
        generated_without_direct_evidence=origin == "generated" and not direct,
        metadata={
            "artifact_refs": {"candidate": "candidates.json"},
            "source_identifiers": dict(identifiers),
            "source_warnings": warnings,
            "missing_data": [],
            "warnings": [],
        },
    )


def _candidate_from_raw_generated(
    raw: Mapping[str, Any],
    *,
    disease_name: str | None,
) -> PortfolioCandidate:
    score_breakdown = _as_mapping(raw.get("score_breakdown"))
    metadata = _as_mapping(raw.get("metadata"))
    novelty = _as_mapping(raw.get("novelty"))
    source_id = _first_string(
        raw.get("generated_id"),
        raw.get("portfolio_candidate_id"),
        raw.get("candidate_id"),
        raw.get("name"),
    )
    candidate_name = (
        _first_string(raw.get("name"), raw.get("candidate_name"), source_id) or "generated"
    )
    warnings = [
        str(item)
        for item in [
            *_as_list(raw.get("warnings")),
            *_as_list(_as_mapping(raw.get("validation")).get("rejection_reasons")),
        ]
    ]
    direct = bool(raw.get("direct_experimental_evidence"))
    return PortfolioCandidate(
        portfolio_candidate_id=source_id or _stable_candidate_id(candidate_name),
        source_candidate_id=source_id,
        candidate_name=candidate_name,
        origin="generated",
        canonical_smiles=_first_string(raw.get("canonical_smiles"), raw.get("smiles")),
        inchi_key=_first_string(raw.get("inchi_key"), raw.get("inchiKey")),
        disease_name=_first_string(raw.get("disease_name"), disease_name),
        target_symbols=_string_list(
            raw.get("target_symbols") or raw.get("conditioned_targets") or raw.get("target_symbol")
        ),
        mechanism_label=_first_string(raw.get("mechanism_label")),
        chemical_series_id=_first_string(
            raw.get("chemical_series_id"), raw.get("diversity_cluster")
        ),
        scaffold_id=_first_string(
            raw.get("scaffold_id"), metadata.get("scaffold_id"), raw.get("diversity_cluster")
        ),
        evidence_score=None,
        generation_score=_score_or_none(
            raw.get("generation_score"),
            raw.get("score"),
            score_breakdown.get("final_generation_score"),
        ),
        developability_score=_score_or_none(
            raw.get("developability_score"),
            score_breakdown.get("developability_score"),
        ),
        experimental_support_score=None,
        predictive_model_score=None,
        structure_score=_score_or_none(raw.get("structure_score"), metadata.get("structure_score")),
        experiment_readiness_score=_score_or_none(
            raw.get("experiment_readiness_score"),
            score_breakdown.get("experiment_readiness_score"),
            _as_mapping(metadata.get("experiment_readiness")).get("score"),
        ),
        uncertainty_score=_score_or_none(
            raw.get("uncertainty_score"),
            score_breakdown.get("uncertainty_score"),
            _as_mapping(metadata.get("uncertainty")).get("active_learning_value"),
        ),
        novelty_score=_score_or_none(
            raw.get("novelty_score"),
            score_breakdown.get("novelty_score"),
            novelty.get("novelty_score"),
        ),
        diversity_features={
            "diversity_cluster": raw.get("diversity_cluster"),
            "descriptors": _as_mapping(raw.get("descriptors")),
        },
        risk_flags=risk_flags_from_text(warnings),
        blocking_risks=blocking_risks_from_text(warnings),
        review_status=None,
        direct_experimental_evidence=direct,
        generated_without_direct_evidence=not direct,
        metadata={
            "artifact_refs": {"candidate": "generated_candidates.json"},
            "source_warnings": warnings,
            "missing_data": [],
            "warnings": [],
            "generated_hypothesis_only": not direct,
        },
    )


def _generated_records_from_payload(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    payload = _as_mapping(payload)
    records: list[Mapping[str, Any]] = []
    for key in (
        "retained_generated_molecules",
        "generated_molecules",
        "generated_molecule_hypotheses",
        "generated_candidates",
        "generated",
        "retained",
    ):
        records.extend(item for item in _as_list(payload.get(key)) if isinstance(item, Mapping))
    return records


class _CandidateIndex:
    def __init__(self, candidates: Sequence[PortfolioCandidate]) -> None:
        self.candidates = list(candidates)
        self.by_key: dict[str, list[PortfolioCandidate]] = defaultdict(list)
        for candidate in candidates:
            self.add(candidate)

    def add(self, candidate: PortfolioCandidate) -> None:
        for key in _candidate_match_keys(candidate):
            self.by_key[key].append(candidate)

    def match(self, raw: Mapping[str, Any]) -> PortfolioCandidate | None:
        keys = _artifact_match_keys(raw)
        for key in keys:
            matches = self.by_key.get(key, [])
            if matches:
                candidate = matches[0]
                _record_identifier_conflicts(candidate, raw)
                return candidate
        return None


def _apply_developability_artifact(
    candidates: Sequence[PortfolioCandidate],
    index: _CandidateIndex,
    payload: Any,
) -> None:
    for raw in _records_from_payload(
        payload, ("assessments", "developability_assessments", "items")
    ):
        candidate = index.match(raw)
        if candidate is None:
            continue
        score = _score_or_none(
            raw.get("developability_score"),
            raw.get("overall_developability_score"),
            raw.get("score"),
        )
        if score is not None:
            candidate.developability_score = score
        risk_level = _first_string(
            raw.get("risk_level"), _as_mapping(raw.get("metadata")).get("risk_level")
        )
        recommendation = _first_string(raw.get("recommendation"), raw.get("triage_recommendation"))
        flag_text = [
            *(
                str(item.get("label") or item.get("category") or item)
                for item in _risk_flag_records(raw)
            ),
            *(str(item) for item in (risk_level, recommendation) if item),
        ]
        _merge_unique(candidate.risk_flags, risk_flags_from_text(flag_text))
        if risk_level and risk_level.lower() in {"critical", "high"}:
            candidate.risk_flags.append(f"{risk_level.lower()}_developability_risk")
        if risk_level and risk_level.lower() == "critical":
            candidate.blocking_risks.append("critical_developability_risk")
        if recommendation and "high_risk" in recommendation.lower():
            candidate.risk_flags.append("high_developability_risk")
        _dedupe_candidate_flags(candidate)
        _add_artifact_ref(candidate, "developability", "developability.json")
        candidate.metadata["developability_artifact"] = dict(raw)


def _apply_experimental_evidence_artifact(
    candidates: Sequence[PortfolioCandidate],
    index: _CandidateIndex,
    payload: Any,
) -> None:
    payload_map = _as_mapping(payload)
    result_records = [
        item for item in _as_list(payload_map.get("results")) if isinstance(item, Mapping)
    ]
    matched_result_ids: dict[str, list[str]] = defaultdict(list)
    for raw in result_records:
        candidate = index.match(raw)
        if candidate is None:
            continue
        result_id = (
            _first_string(raw.get("result_id"), raw.get("source_record_id")) or "unknown-result"
        )
        matched_result_ids[candidate.portfolio_candidate_id].append(result_id)
        candidate.direct_experimental_evidence = True
        candidate.generated_without_direct_evidence = False
        candidate.experimental_support_score = _experimental_summary_score(raw)
        _add_artifact_ref(candidate, "experimental_evidence", "experimental_evidence.json")
    for summary_key in ("candidate_summaries", "generated_summaries"):
        summaries = _as_mapping(payload_map.get(summary_key))
        for candidate_name, summary_raw in summaries.items():
            summary = _as_mapping(summary_raw)
            candidate = index.match({"candidate_name": candidate_name})
            if candidate is None:
                continue
            candidate.direct_experimental_evidence = True
            candidate.generated_without_direct_evidence = False
            candidate.experimental_support_score = _experimental_summary_score(summary)
            _add_artifact_ref(candidate, "experimental_evidence", "experimental_evidence.json")
            result_ids = [
                *[str(item) for item in _as_list(summary.get("best_supporting_results"))],
                *[str(item) for item in _as_list(summary.get("key_negative_results"))],
            ]
            if result_ids:
                matched_result_ids[candidate.portfolio_candidate_id].extend(result_ids)
    for candidate in candidates:
        result_ids = sorted(set(matched_result_ids.get(candidate.portfolio_candidate_id, [])))
        if result_ids:
            candidate.metadata["exact_experimental_evidence_result_ids"] = result_ids


def _apply_model_predictions_artifact(
    candidates: Sequence[PortfolioCandidate],
    index: _CandidateIndex,
    payload: Any,
) -> None:
    for raw in _records_from_payload(payload, ("predictions", "items", "records")):
        candidate = index.match(raw)
        if candidate is None:
            continue
        score = _score_or_none(
            raw.get("predicted_probability"),
            raw.get("prediction_score"),
            raw.get("confidence"),
        )
        if score is not None:
            candidate.predictive_model_score = score
        candidate.metadata.setdefault("model_predictions", []).append(dict(raw))
        candidate.metadata["model_predictions_are_not_evidence"] = True
        _add_artifact_ref(candidate, "model_predictions", "model_predictions.json")


def _apply_structure_artifact(
    candidates: Sequence[PortfolioCandidate],
    index: _CandidateIndex,
    payload: Any,
) -> None:
    for raw in _records_from_payload(
        payload,
        ("structure_aware_assessments", "assessments", "items", "records"),
    ):
        candidate = index.match(raw)
        if candidate is None:
            continue
        score = _score_or_none(
            raw.get("structure_score"),
            raw.get("consensus_score"),
            raw.get("score"),
        )
        if score is not None:
            candidate.structure_score = score
        candidate.metadata.setdefault("structure_assessments", []).append(dict(raw))
        candidate.metadata["structure_score_is_not_binding_evidence"] = True
        _add_artifact_ref(
            candidate, "structure_aware_assessments", "structure_aware_assessments.json"
        )


def _apply_review_artifact(
    candidates: Sequence[PortfolioCandidate],
    index: _CandidateIndex,
    payload: Any,
) -> None:
    for raw in _records_from_payload(payload, ("review_items", "items", "records")):
        candidate = index.match(raw)
        if candidate is None:
            continue
        status = _first_string(raw.get("review_status"), raw.get("status"), raw.get("decision"))
        if status:
            candidate.review_status = status
        candidate.metadata.setdefault("review_records", []).append(dict(raw))
        candidate.metadata["review_decisions_are_not_evidence"] = True
        _add_artifact_ref(candidate, "review_queue", "review_queue.json")


def _apply_active_learning_artifact(
    candidates: Sequence[PortfolioCandidate],
    index: _CandidateIndex,
    payload: Any,
) -> None:
    for raw in _records_from_payload(payload, ("suggestions", "items", "records")):
        candidate = index.match(raw)
        if candidate is None:
            continue
        if candidate.uncertainty_score is None:
            candidate.uncertainty_score = _score_or_none(raw.get("uncertainty_score"))
        candidate.metadata.setdefault("active_learning_suggestions", []).append(dict(raw))
        _add_artifact_ref(candidate, "active_learning_batch", "active_learning_batch.json")


def _apply_readiness_artifact(
    candidates: Sequence[PortfolioCandidate],
    index: _CandidateIndex,
    payload: Any,
    *,
    artifact_name: str,
) -> None:
    for raw in _records_from_payload(payload, ("candidates", "items", "records")):
        candidate = index.match(raw)
        if candidate is None:
            continue
        score = _score_or_none(raw.get("readiness_score"), raw.get("experiment_readiness_score"))
        if score is not None:
            candidate.experiment_readiness_score = score
        _merge_unique(
            candidate.blocking_risks, [str(item) for item in _as_list(raw.get("blocking_risks"))]
        )
        _merge_unique(candidate.risk_flags, risk_flags_from_text(candidate.blocking_risks))
        candidate.metadata.setdefault("readiness_records", []).append(dict(raw))
        _add_artifact_ref(candidate, "experiment_readiness", artifact_name)


def _apply_external_mappings(
    candidates: Sequence[PortfolioCandidate],
    index: _CandidateIndex,
    payload: Any,
    *,
    artifact_name: str,
) -> None:
    for raw in _records_from_payload(payload, ("mappings", "items", "records")):
        candidate = index.match(raw)
        if candidate is None:
            continue
        candidate.metadata.setdefault("external_integration_mappings", []).append(dict(raw))
        _add_artifact_ref(candidate, "external_mappings", artifact_name)


def _finalize_artifact_candidate(candidate: PortfolioCandidate) -> None:
    missing = [field for field in PORTFOLIO_SCORE_FIELDS if getattr(candidate, field) is None]
    candidate.metadata["missing_data"] = sorted(
        set([*candidate.metadata.get("missing_data", []), *missing])
    )
    candidate.risk_flags = sorted(set(candidate.risk_flags))
    candidate.blocking_risks = sorted(set(candidate.blocking_risks))
    if candidate.origin == "generated" and not candidate.direct_experimental_evidence:
        candidate.generated_without_direct_evidence = True
    if candidate.origin == "generated" and candidate.direct_experimental_evidence:
        candidate.generated_without_direct_evidence = False


def _index_results(experimental_results: Sequence[AssayResult]) -> dict[str, list[AssayResult]]:
    index: dict[str, list[AssayResult]] = defaultdict(list)
    for result in experimental_results:
        for key in {
            result.candidate_id,
            result.candidate_name,
            result.canonical_smiles,
            result.inchi_key,
        }:
            if key:
                index[str(key).lower()].append(result)
    return index


def _matching_results(
    candidate_id: str,
    name: str,
    canonical_smiles: str | None,
    results_by_key: Mapping[str, list[AssayResult]],
) -> list[AssayResult]:
    matched: dict[str, AssayResult] = {}
    for key in {candidate_id, name, canonical_smiles}:
        if not key:
            continue
        for result in results_by_key.get(str(key).lower(), []):
            matched[result.result_id] = result
    return sorted(matched.values(), key=lambda result: result.result_id)


def _experimental_feedback_score(results: Sequence[AssayResult]) -> float:
    if not results:
        return 0.0
    passed = [result for result in results if result.qc_status in {"passed", "partial"}]
    if not passed:
        return 0.1
    positives = sum(1 for result in passed if result.outcome_label == "positive")
    negatives = sum(1 for result in passed if result.outcome_label == "negative")
    inconclusive = sum(1 for result in passed if result.outcome_label == "inconclusive")
    return _clamp(
        (0.5 * positives + 0.2 * inconclusive) / max(1, positives + negatives + inconclusive)
    )


def _existing_candidate_id(candidate: MoleculeCandidate) -> str:
    for key in ("chembl", "chembl_id", "pubchem_cid", "cid", "inchikey", "inchi_key"):
        value = candidate.identifiers.get(key)
        if value:
            return str(value)
    return str(uuid5(NAMESPACE_URL, f"molecule-ranker:v1.4:candidate:{candidate.name}"))


def _developability_score(assessment: Any | None) -> float | None:
    if assessment is None:
        return None
    return _bounded_number(getattr(assessment, "developability_score", None))


def _breakdown_score(breakdown: Any | None, key: str) -> float | None:
    if breakdown is None:
        return None
    return _bounded_number(getattr(breakdown, key, None))


def _metadata_score(metadata: Mapping[str, Any], *path: str) -> float | None:
    value: Any = metadata
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return _bounded_number(value)


def _model_prediction_score(metadata: Mapping[str, Any]) -> float | None:
    raw = metadata.get("model_predictions")
    if not isinstance(raw, list) or not raw:
        return None
    scores = [
        _bounded_number(item.get("prediction_score"))
        for item in raw
        if isinstance(item, Mapping) and item.get("prediction_score") is not None
    ]
    scores = [score for score in scores if score is not None]
    return round(sum(scores) / len(scores), 3) if scores else None


def _metadata_string(metadata: Mapping[str, Any], key: str) -> str | None:
    value = metadata.get(key)
    return str(value) if value else None


def _string_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def _bounded_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, int | float):
        return round(_clamp(float(value)), 3)
    return None


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _records_from_payload(payload: Any, keys: Sequence[str]) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    payload_map = _as_mapping(payload)
    records: list[Mapping[str, Any]] = []
    for key in keys:
        value = payload_map.get(key)
        if isinstance(value, list):
            records.extend(item for item in value if isinstance(item, Mapping))
        elif isinstance(value, Mapping):
            records.extend(item for item in value.values() if isinstance(item, Mapping))
    return records


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if value:
        return [str(value)]
    return []


def _first_string(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
        if not isinstance(value, str):
            return str(value)
    return None


def _score_or_none(*values: Any) -> float | None:
    for value in values:
        score = _bounded_number(value)
        if score is not None:
            return score
    return None


def _stable_candidate_id(candidate_name: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"molecule-ranker:v1.4:candidate:{candidate_name}"))


def _candidate_match_keys(candidate: PortfolioCandidate) -> set[str]:
    keys = {
        candidate.portfolio_candidate_id,
        candidate.source_candidate_id,
        candidate.candidate_name,
        candidate.canonical_smiles,
        candidate.inchi_key,
    }
    return {str(key).lower() for key in keys if key}


def _artifact_match_keys(raw: Mapping[str, Any]) -> list[str]:
    keys = [
        raw.get("portfolio_candidate_id"),
        raw.get("source_candidate_id"),
        raw.get("candidate_id"),
        raw.get("generated_id"),
        raw.get("molecule_id"),
        raw.get("mapped_candidate_id"),
        raw.get("linked_candidate_id"),
        raw.get("external_id"),
        raw.get("candidate_name"),
        raw.get("molecule_name"),
        raw.get("name"),
        raw.get("canonical_smiles"),
        raw.get("smiles"),
        raw.get("inchi_key"),
        raw.get("inchiKey"),
    ]
    linked = _as_mapping(raw.get("metadata")).get("linked_candidate_id")
    if linked:
        keys.insert(0, linked)
    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if not key:
            continue
        normalized = str(key).lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _record_identifier_conflicts(
    candidate: PortfolioCandidate,
    raw: Mapping[str, Any],
) -> None:
    conflicts = []
    raw_smiles = _first_string(raw.get("canonical_smiles"), raw.get("smiles"))
    if raw_smiles and candidate.canonical_smiles and raw_smiles != candidate.canonical_smiles:
        conflicts.append(
            {
                "field": "canonical_smiles",
                "candidate_value": candidate.canonical_smiles,
                "artifact_value": raw_smiles,
            }
        )
    raw_inchi = _first_string(raw.get("inchi_key"), raw.get("inchiKey"))
    if raw_inchi and candidate.inchi_key and raw_inchi != candidate.inchi_key:
        conflicts.append(
            {
                "field": "inchi_key",
                "candidate_value": candidate.inchi_key,
                "artifact_value": raw_inchi,
            }
        )
    if conflicts:
        candidate.metadata.setdefault("identifier_conflicts", []).extend(conflicts)
        candidate.metadata.setdefault("warnings", []).append("conflicting_identifiers")


def _risk_flag_records(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for key in (
        "admet_property_flags",
        "toxicity_risk_flags",
        "medicinal_chemistry_alerts",
        "chemical_liability_flags",
        "structure_quality_flags",
        "risk_flags",
        "alerts",
    ):
        records.extend(item for item in _as_list(raw.get(key)) if isinstance(item, Mapping))
    return records


def _merge_unique(target: list[str], values: Sequence[str]) -> None:
    target[:] = sorted({*target, *(value for value in values if value)})


def _dedupe_candidate_flags(candidate: PortfolioCandidate) -> None:
    candidate.risk_flags = sorted(set(candidate.risk_flags))
    candidate.blocking_risks = sorted(set(candidate.blocking_risks))


def _add_artifact_ref(
    candidate: PortfolioCandidate,
    key: str,
    artifact_name: str,
) -> None:
    refs = candidate.metadata.setdefault("artifact_refs", {})
    if isinstance(refs, dict):
        refs[key] = artifact_name


def _experimental_summary_score(raw: Mapping[str, Any]) -> float | None:
    if raw.get("result_count") == 0:
        return 0.0
    if raw.get("positive_count") is not None or raw.get("negative_count") is not None:
        positives = int(raw.get("positive_count") or 0)
        negatives = int(raw.get("negative_count") or 0)
        inconclusive = int(raw.get("inconclusive_count") or 0)
        return _clamp(
            (0.5 * positives + 0.2 * inconclusive) / max(1, positives + negatives + inconclusive)
        )
    outcome = str(raw.get("outcome_label") or "").lower()
    if outcome == "positive":
        return 0.5
    if outcome == "inconclusive":
        return 0.2
    if outcome == "negative":
        return 0.0
    return _score_or_none(raw.get("experimental_support_score"), raw.get("confidence"))
