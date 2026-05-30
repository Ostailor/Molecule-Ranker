from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from molecule_ranker.structure.schemas import StructureRecord, StructureSelection


def select_structure(
    structures: list[StructureRecord],
    *,
    target_symbol: str,
    workflow: str = "structure_aware_scoring",
    related_ligand_ids: set[str] | None = None,
    preferred_organism: str | None = "Homo sapiens",
    allow_user_supplied: bool = False,
    strict_structure_selection: bool = False,
    min_coverage: float = 0.5,
    min_target_mapping_confidence: float = 0.5,
) -> StructureSelection:
    evaluations = [
        _evaluate_structure(
            record,
            target_symbol=target_symbol,
            related_ligand_ids=related_ligand_ids or set(),
            preferred_organism=preferred_organism,
            allow_user_supplied=allow_user_supplied,
            min_coverage=min_coverage,
            min_target_mapping_confidence=min_target_mapping_confidence,
        )
        for record in structures
    ]
    acceptable = [evaluation for evaluation in evaluations if evaluation.acceptable]
    if not acceptable:
        rejected = [_rejection_payload(evaluation) for evaluation in evaluations]
        warnings = [
            "Structure-aware scoring skipped: no acceptable target structure was available."
        ]
        if strict_structure_selection:
            raise ValueError("No acceptable structure was available for structure-aware scoring.")
        return StructureSelection(
            selection_id=_selection_id(target_symbol, "unavailable"),
            target_symbol=target_symbol,
            selected_structure_id="unavailable",
            selected_chain_ids=[],
            selection_reason="No acceptable structure was available.",
            confidence=0.0,
            rejected_structures=rejected,
            warnings=warnings,
            metadata={
                "workflow": workflow,
                "selection_policy": "v1.3_conservative_structure_selection",
                "applicability_domain": "unavailable",
                "structure_aware_scoring_skipped": True,
            },
        )

    selected = max(acceptable, key=lambda evaluation: evaluation.sort_key)
    rejected = [
        _rejection_payload(evaluation)
        for evaluation in evaluations
        if evaluation.record.structure_id != selected.record.structure_id
    ]
    warnings = list(selected.warnings)
    if selected.applicability_domain in {
        "lower_confidence_predicted_structure",
        "weak_or_unknown_structure",
    }:
        warnings.append(
            "Only a lower-confidence or weak structure was selected; use for review only."
        )
    return StructureSelection(
        selection_id=_selection_id(target_symbol, selected.record.structure_id),
        target_symbol=target_symbol,
        selected_structure_id=selected.record.structure_id,
        selected_chain_ids=selected.selected_chain_ids,
        selection_reason=selected.reason,
        confidence=selected.confidence,
        rejected_structures=rejected,
        warnings=sorted(set(warnings)),
        metadata={
            "workflow": workflow,
            "selection_policy": "v1.3_conservative_structure_selection",
            "applicability_domain": selected.applicability_domain,
            "selection_factors": selected.factors,
            "structure_aware_scoring_skipped": False,
        },
    )


@dataclass(frozen=True)
class _Evaluation:
    record: StructureRecord
    acceptable: bool
    reasons: list[str]
    warnings: list[str]
    reason: str
    selected_chain_ids: list[str]
    confidence: float
    applicability_domain: str
    factors: dict[str, Any]
    sort_key: tuple[float, ...]


def _evaluate_structure(
    record: StructureRecord,
    *,
    target_symbol: str,
    related_ligand_ids: set[str],
    preferred_organism: str | None,
    allow_user_supplied: bool,
    min_coverage: float,
    min_target_mapping_confidence: float,
) -> _Evaluation:
    target_mapping_confidence = _target_mapping_confidence(record)
    coverage = _coverage_score(record)
    binding_region_confidence = _binding_region_confidence(record)
    chain_completeness = _chain_completeness(record)
    mutation_burden = len(record.mutations)
    relevant_ligand = _has_relevant_ligand(record, related_ligand_ids)
    any_ligand = bool(record.ligands)
    binding_site_evidence = bool(record.metadata.get("binding_site_evidence")) or relevant_ligand
    organism_score = _organism_score(record, preferred_organism)
    resolution_score = _resolution_score(record)
    method_score = _method_score(record)
    provenance_score = _provenance_score(record)
    predicted_confidence = _predicted_confidence(record, binding_region_confidence)

    rejection_reasons: list[str] = []
    warnings: list[str] = []
    if record.target_symbol.upper() != target_symbol.upper():
        rejection_reasons.append("target_symbol_mismatch")
    if target_mapping_confidence < min_target_mapping_confidence:
        rejection_reasons.append("low_target_mapping_confidence")
    if coverage < min_coverage:
        rejection_reasons.append("poor_sequence_coverage")
    if record.structure_type == "user_supplied" and not allow_user_supplied:
        rejection_reasons.append("user_supplied_structure_not_explicitly_enabled")
    if record.structure_type == "experimental" and not record.chains:
        rejection_reasons.append("missing_chain_annotation")

    bucket, reason, applicability_domain = _ranking_bucket(
        record,
        relevant_ligand=relevant_ligand,
        any_ligand=any_ligand,
        binding_site_evidence=binding_site_evidence,
        resolution_score=resolution_score,
        coverage=coverage,
        binding_region_confidence=binding_region_confidence,
        allow_user_supplied=allow_user_supplied,
    )
    if bucket <= 0:
        rejection_reasons.append("structure_not_suitable_for_requested_workflow")

    acceptable = not rejection_reasons
    confidence = _selection_confidence(
        bucket=bucket,
        target_mapping_confidence=target_mapping_confidence,
        coverage=coverage,
        chain_completeness=chain_completeness,
        resolution_score=resolution_score,
        organism_score=organism_score,
        method_score=method_score,
        provenance_score=provenance_score,
        predicted_confidence=predicted_confidence,
        mutation_burden=mutation_burden,
        structure_type=record.structure_type,
    )
    if record.structure_type == "predicted":
        warnings.append(
            "Predicted structures are lower-confidence than suitable experimental structures."
        )
    if confidence < 0.55 and acceptable:
        warnings.append("Selected structure is weak; structure-aware outputs require review.")

    factors = {
        "target_mapping_confidence": target_mapping_confidence,
        "organism_score": organism_score,
        "coverage": coverage,
        "resolution_score": resolution_score,
        "ligand_presence": any_ligand,
        "relevant_ligand": relevant_ligand,
        "binding_site_evidence": binding_site_evidence,
        "mutation_burden": mutation_burden,
        "chain_completeness": chain_completeness,
        "method_score": method_score,
        "provenance_score": provenance_score,
        "predicted_binding_region_confidence": binding_region_confidence,
    }
    sort_key = (
        float(bucket),
        target_mapping_confidence,
        organism_score,
        coverage,
        resolution_score,
        1.0 if relevant_ligand else 0.0,
        1.0 if binding_site_evidence else 0.0,
        -float(mutation_burden),
        chain_completeness,
        method_score,
        provenance_score,
        predicted_confidence,
        confidence,
    )
    return _Evaluation(
        record=record,
        acceptable=acceptable,
        reasons=rejection_reasons or ["lower_ranked_than_selected_structure"],
        warnings=warnings,
        reason=reason,
        selected_chain_ids=_selected_chains(record),
        confidence=confidence if acceptable else 0.0,
        applicability_domain=applicability_domain if acceptable else "unavailable",
        factors=factors,
        sort_key=sort_key,
    )


def _ranking_bucket(
    record: StructureRecord,
    *,
    relevant_ligand: bool,
    any_ligand: bool,
    binding_site_evidence: bool,
    resolution_score: float,
    coverage: float,
    binding_region_confidence: float,
    allow_user_supplied: bool,
) -> tuple[int, str, str]:
    if record.structure_type == "experimental":
        if relevant_ligand and binding_site_evidence:
            return (
                6,
                "Experimental co-crystal structure with relevant ligand selected.",
                "suitable_experimental_structure",
            )
        if not any_ligand and resolution_score >= 0.65 and coverage >= 0.75:
            return (
                5,
                "High-quality experimental apo structure selected.",
                "suitable_experimental_structure",
            )
        if coverage >= 0.7:
            return (
                4,
                "Experimental structure with suitable coverage and chain selected.",
                "suitable_experimental_structure",
            )
        return (0, "Experimental structure was too weak for selection.", "unavailable")
    if record.structure_type == "predicted":
        if binding_region_confidence >= 0.7 and coverage >= 0.7:
            return (
                3,
                "Predicted structure selected with lower confidence in binding region.",
                "lower_confidence_predicted_structure",
            )
        if coverage >= 0.6:
            return (
                1,
                "Weak predicted structure selected because no stronger structure was "
                "available.",
                "weak_or_unknown_structure",
            )
        return (0, "Predicted structure confidence or coverage was too weak.", "unavailable")
    if record.structure_type == "user_supplied" and allow_user_supplied:
        return (
            2,
            "User-supplied structure selected because it was explicitly configured.",
            "weak_or_unknown_structure",
        )
    return (0, "Structure type was not suitable for selection.", "unavailable")


def _target_mapping_confidence(record: StructureRecord) -> float:
    for source in (record.quality_metrics, record.metadata):
        value = source.get("target_mapping_confidence")
        parsed = _as_float(value)
        if parsed is not None:
            return _bounded(parsed)
    return 0.9 if record.target_identifiers else 0.55


def _coverage_score(record: StructureRecord) -> float:
    candidates = []
    for key in ("overall", "sequence_coverage", "target_coverage", "binding_region"):
        parsed = _as_float(record.coverage.get(key))
        if parsed is not None:
            candidates.append(parsed)
    for value in record.coverage.values():
        parsed = _as_float(value)
        if parsed is not None:
            candidates.append(parsed)
    return _bounded(max(candidates)) if candidates else 0.65


def _binding_region_confidence(record: StructureRecord) -> float:
    for source in (record.quality_metrics, record.coverage, record.metadata):
        for key in (
            "predicted_binding_region_confidence",
            "binding_region_confidence",
            "binding_region",
        ):
            parsed = _as_float(source.get(key))
            if parsed is not None:
                return _bounded(parsed)
    normalized = _as_float(record.quality_metrics.get("normalized_global_confidence"))
    if normalized is not None:
        return _bounded(normalized)
    return _coverage_score(record)


def _chain_completeness(record: StructureRecord) -> float:
    raw = record.metadata.get("chain_completeness")
    if isinstance(raw, dict):
        values = [_as_float(value) for value in raw.values()]
        numeric = [value for value in values if value is not None]
        if numeric:
            return _bounded(max(numeric))
    return 0.8 if record.chains else 0.0


def _has_relevant_ligand(record: StructureRecord, related_ligand_ids: set[str]) -> bool:
    for ligand in record.ligands:
        ligand_id = str(
            ligand.get("ligand_id")
            or ligand.get("id")
            or ligand.get("chem_comp_id")
            or ""
        ).upper()
        relationship = str(ligand.get("relationship") or "").lower()
        if related_ligand_ids and ligand_id in {item.upper() for item in related_ligand_ids}:
            return True
        if relationship in {"relevant", "related", "co_crystal", "known_ligand"}:
            return True
    return False


def _organism_score(record: StructureRecord, preferred_organism: str | None) -> float:
    if not preferred_organism or not record.organism:
        return 0.7
    return 1.0 if record.organism.lower() == preferred_organism.lower() else 0.55


def _resolution_score(record: StructureRecord) -> float:
    if record.resolution_angstrom is None:
        return 0.0
    return _bounded((3.5 - record.resolution_angstrom) / 2.5)


def _method_score(record: StructureRecord) -> float:
    method = str(record.experimental_method or "").lower()
    if "x-ray" in method or "diffraction" in method:
        return 1.0
    if "cryo" in method or "electron" in method:
        return 0.85
    if "nmr" in method:
        return 0.65
    if "computed" in method:
        return 0.45
    return 0.5


def _provenance_score(record: StructureRecord) -> float:
    score = 0.5
    if record.release_date:
        score += 0.15
    if record.url:
        score += 0.15
    if record.metadata.get("raw_metadata_artifact"):
        score += 0.1
    if record.source in {"RCSB_PDB", "AlphaFold_DB"}:
        score += 0.1
    return _bounded(score)


def _predicted_confidence(record: StructureRecord, binding_region_confidence: float) -> float:
    if record.structure_type != "predicted":
        return 0.0
    return min(0.65, binding_region_confidence)


def _selection_confidence(
    *,
    bucket: int,
    target_mapping_confidence: float,
    coverage: float,
    chain_completeness: float,
    resolution_score: float,
    organism_score: float,
    method_score: float,
    provenance_score: float,
    predicted_confidence: float,
    mutation_burden: int,
    structure_type: str,
) -> float:
    base = (
        0.30 * target_mapping_confidence
        + 0.20 * coverage
        + 0.15 * chain_completeness
        + 0.10 * organism_score
        + 0.10 * method_score
        + 0.10 * provenance_score
        + 0.05 * max(resolution_score, predicted_confidence)
    )
    base -= min(0.2, 0.03 * mutation_burden)
    base += min(0.1, max(bucket, 0) * 0.01)
    cap = 0.65 if structure_type == "predicted" else 0.9
    if structure_type == "user_supplied":
        cap = 0.6
    return round(min(cap, _bounded(base)), 3)


def _selected_chains(record: StructureRecord) -> list[str]:
    completeness = record.metadata.get("chain_completeness")
    if isinstance(completeness, dict) and completeness:
        best = max(completeness.items(), key=lambda item: _as_float(item[1]) or 0.0)
        return [str(best[0])]
    return list(record.chains[:1])


def _rejection_payload(evaluation: _Evaluation) -> dict[str, Any]:
    return {
        "structure_id": evaluation.record.structure_id,
        "source": evaluation.record.source,
        "structure_type": evaluation.record.structure_type,
        "reasons": evaluation.reasons,
        "score_factors": evaluation.factors,
    }


def _selection_id(target_symbol: str, structure_id: str) -> str:
    safe_target = _safe_id(target_symbol)
    safe_structure = _safe_id(structure_id)
    return f"structure-selection-{safe_target}-{safe_structure}"


def _safe_id(value: str) -> str:
    return "".join(character.lower() if character.isalnum() else "-" for character in value)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bounded(value: float) -> float:
    return max(0.0, min(1.0, value))


__all__ = ["select_structure"]
